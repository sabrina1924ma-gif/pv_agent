"""
REST API 路由定义。

所有路由均以 /api/v1 为前缀。请求流经 Agent 图，
由 Agent 图协调意图分类、工具调用和报告生成。

HTTP 上下文：graph.ainvoke() 在 FastAPI 的事件循环中运行。
LangGraph 节点内的异步操作（LLM 调用、工具 I/O）通过 asyncio
协作调度正确参与同一事件循环。

端点：
    POST   /api/v1/chat                   — 非流式聊天，返回完整响应
    POST   /api/v1/sessions               — 创建新会话
    GET    /api/v1/sessions               — 列出所有会话摘要
    GET    /api/v1/sessions/{id}          — 获取单个会话元数据
    GET    /api/v1/sessions/{id}/messages — 获取会话的聊天记录
    DELETE /api/v1/sessions/{id}          — 清除会话上下文
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app.agent.graph import get_graph
from app.agent.schema import AgentState
from app.api.dependencies import get_current_user, get_optional_user, AuthUser
from app.api.schemas import (
    AddMemoryNoteRequest,
    UpdateProfileRequest,
    UserProfileResponse,
)
from loguru import logger

api_router = APIRouter(tags=["Agent"])


# ============================================================================
# 共享工具函数
# ============================================================================


def _schedule_memory_indexing(
    user_query: str,
    assistant_response: str,
    user_id: str,
    session_id: str,
    intent: str = "",
    tools_used: list[str] | None = None,
) -> None:
    """
    触发长期记忆索引（fire-and-forget）。

    在 asyncio 后台任务中运行，不阻塞 HTTP/WS 响应。
    失败时静默降级——索引失败不影响主流程。
    """
    import asyncio

    async def _do_index() -> None:
        try:
            from app.rag.memory_indexer import MemoryIndexer

            indexer = MemoryIndexer()
            await indexer.index_turn(
                user_query=user_query,
                assistant_response=assistant_response,
                user_id=user_id,
                session_id=session_id,
                intent=intent,
                tools_used=tools_used or [],
            )
        except Exception:
            logger.warning("Memory indexing skipped (RAG not available)")

    try:
        asyncio.create_task(_do_index())
    except RuntimeError:
        # 没有运行中的 event loop（测试环境等）
        pass


# ============================================================================
# 请求 / 响应模式
# ============================================================================

class ChatRequest(BaseModel):
    """来自用户的聊天消息。"""

    message: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description="User's natural-language query about power/equipment data",
    )
    station_id: str | None = Field(
        default=None,
        description="Optional station/device identifier to scope the query",
    )


class ChatResponse(BaseModel):
    """Agent 处理完用户查询后的响应。"""

    session_id: str = Field(..., description="Session identifier for follow-up queries")
    message: str = Field(..., description="Agent's text response / summary")
    intent: str | None = Field(
        default=None, description="Classified intent of the user's query"
    )
    report_md: str | None = Field(
        default=None,
        description="Generated Markdown report (populated when intent triggers report generation)",
    )
    tool_results: list[dict[str, Any]] | None = Field(
        default=None,
        description="Raw results from invoked tools, for debugging and display",
    )
    error: str | None = Field(
        default=None,
        description="Error message if the agent encountered an unrecoverable error",
    )


class SessionInfo(BaseModel):
    """对话会话的元数据。"""

    session_id: str = Field(..., description="Session identifier")
    message_count: int = Field(
        default=0, description="Number of messages in this session"
    )
    created_at: str | None = Field(
        default=None, description="Session creation timestamp (ISO 8601)"
    )


class SessionSummary(BaseModel):
    """会话列表中的摘要条目。"""

    session_id: str = Field(..., description="Session identifier")
    title: str = Field(default="", description="First user message (truncated to 60 chars)")
    message_count: int = Field(default=0, description="Number of messages in this session")
    created_at: str | None = Field(default=None, description="Session creation timestamp (ISO 8601)")
    last_active: str | None = Field(default=None, description="Last message timestamp (ISO 8601)")


class SessionCreateResponse(BaseModel):
    """创建会话的响应。"""

    session_id: str = Field(..., description="Newly created session identifier")
    created_at: str = Field(..., description="Creation timestamp (ISO 8601)")


class MessageItem(BaseModel):
    """单条聊天消息（用于会话历史回放）。"""

    id: str = Field(..., description="Message unique identifier")
    role: str = Field(..., description="Message role: user, assistant, system, or tool")
    content: str = Field(..., description="Full message content")
    intent: str | None = Field(default=None, description="Classified intent label")
    created_at: str = Field(..., description="Message creation timestamp (ISO 8601)")


class SessionMessagesResponse(BaseModel):
    """会话消息列表响应。"""

    session_id: str = Field(..., description="Session identifier")
    messages: list[MessageItem] = Field(default_factory=list, description="Messages in chronological order")


# ============================================================================
# 路由 — 聊天
# ============================================================================

@api_router.post("/chat", response_model=ChatResponse, status_code=200)
async def chat(request: Request, body: ChatRequest) -> ChatResponse:
    """
    非流式聊天端点（需要认证）。

    通过完整的 LangGraph Agent 管线处理用户消息并同步返回完整响应。
    """
    # 获取当前用户（强制认证）
    user = await get_current_user(request)

    session_id: str = getattr(request.state, "session_id", "unknown")
    request_id: str = getattr(request.state, "request_id", "unknown")

    logger.info(
        f"POST /chat user={user.user_id} session={session_id} "
        f"station={body.station_id} msg_len={len(body.message)}"
    )

    # ------------------------------------------------------------------
    # 1. 构建初始图状态
    # ------------------------------------------------------------------
    initial_state: AgentState = {
        "messages": [HumanMessage(content=body.message)],
        "session_id": session_id,
        "station_id": body.station_id,
        "user_id": str(user.user_id),
    }

    # ------------------------------------------------------------------
    # 2. 调用 Agent 图
    # ------------------------------------------------------------------
    try:
        graph = get_graph()
        result = await graph.ainvoke(initial_state)
    except Exception as exc:
        logger.exception(f"Graph invocation failed for session={session_id}")
        raise HTTPException(
            status_code=500,
            detail="Agent processing failed. Please try again.",
        ) from exc

    # ------------------------------------------------------------------
    # 3. 从图状态中提取结果
    # ------------------------------------------------------------------
    intent: str | None = result.get("intent")
    report_md: str | None = result.get("report_md")
    tool_results: list[dict[str, Any]] | None = result.get("tool_results")
    error: str | None = result.get("error")

    # 构建用户友好的摘要消息
    if error:
        summary = f"处理请求时出现错误: {error}"
    elif report_md:
        first_line = report_md.split("\n", 1)[0] if report_md else ""
        summary = first_line[:200] if first_line else "报告已生成"
    else:
        summary = f"已处理您的请求 (意图: {intent or '未分类'})"

    # 持久化本轮对话（用户隔离）
    try:
        from app.storage.db import get_db_manager
        from app.storage.repositories import ChatHistoryRepository, UserProfileRepository

        db = get_db_manager()
        if await db.health_check():
            async with db.session_context() as db_session:
                repo = ChatHistoryRepository(db_session)
                await repo.save_turn(
                    session_id=session_id,
                    user_message=body.message,
                    assistant_message=report_md or summary,
                    user_id=user.user_id,
                    station_id=body.station_id,
                    intent=intent,
                )
                # 更新用户常访问电站
                if body.station_id:
                    profile_repo = UserProfileRepository(db_session)
                    await profile_repo.update_frequent_stations(
                        user.user_id, body.station_id
                    )
    except Exception:
        logger.exception("Failed to persist chat turn")

    # --- 长期记忆索引（RAG）---
    _schedule_memory_indexing(
        user_query=body.message,
        assistant_response=report_md or summary,
        user_id=str(user.user_id),
        session_id=session_id,
        intent=intent or "",
        tools_used=[r["tool_name"] for r in (tool_results or [])],
    )

    return ChatResponse(
        session_id=session_id,
        message=summary,
        intent=intent,
        report_md=report_md,
        tool_results=tool_results,
        error=error,
    )


# ============================================================================
# 路由 — 会话管理
# ============================================================================

@api_router.post("/sessions", response_model=SessionCreateResponse, status_code=201)
async def create_session(request: Request) -> SessionCreateResponse:
    """
    创建一个新的聊天会话。

    生成新的 UUID 作为 session_id 并返回。客户端应将此 ID
    用于后续的聊天和 WebSocket 连接。

    注意：会话在首次发送消息前不会持久化到数据库。
    """
    from datetime import datetime, timezone

    session_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()

    logger.info(f"Created session {session_id}")
    return SessionCreateResponse(session_id=session_id, created_at=created_at)


@api_router.get("/sessions", response_model=list[SessionSummary])
async def list_sessions(request: Request) -> list[SessionSummary]:
    """
    列出当前用户的会话摘要，按最近活跃时间降序排列。

    过滤条件：只返回当前认证用户创建的会话。
    若数据库不可用则返回空列表。
    """
    user = await get_current_user(request)
    logger.info(f"GET /sessions user={user.user_id}")

    from app.storage.db import get_db_manager
    from app.storage.repositories import ChatHistoryRepository

    db = get_db_manager()
    if not await db.health_check():
        logger.warning("Database not available — returning empty session list")
        return []

    try:
        async with db.session_context() as session:
            repo = ChatHistoryRepository(session)
            rows = await repo.list_sessions(limit=50, user_id=user.user_id)
    except Exception:
        logger.exception("Failed to list sessions")
        return []

    return [SessionSummary(**row) for row in rows]


@api_router.get("/sessions/{session_id}/messages", response_model=SessionMessagesResponse)
async def get_session_messages(session_id: str, request: Request) -> SessionMessagesResponse:
    """
    获取指定会话的完整聊天记录，按时间升序排列（需要认证）。

    用于会话切换时回显历史消息。
    仅返回当前用户拥有的会话。
    """
    user = await get_current_user(request)

    from uuid import UUID

    try:
        UUID(session_id)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid session_id format: {session_id!r}",
        )

    logger.info(f"GET /sessions/{session_id}/messages user={user.user_id}")

    from app.storage.db import get_db_manager
    from app.storage.repositories import ChatHistoryRepository

    db = get_db_manager()
    if not await db.health_check():
        logger.warning("Database not available — returning empty messages")
        return SessionMessagesResponse(session_id=session_id, messages=[])

    try:
        async with db.session_context() as session:
            repo = ChatHistoryRepository(session)
            # 安全校验：确保此会话包含当前用户的消息
            records = await repo.get_by_session(session_id, limit=200, offset=0)
            if len(records) > 0:
                # 已有消息的会话 → 检查所有权（从记录中查找第一条匹配）
                belongs = any(
                    r.user_id is not None and str(r.user_id) == str(user.user_id)
                    for r in records
                )
                if not belongs:
                    # 记录中无人匹配 → 再次查询确认
                    belongs = await repo.session_has_messages_from_user(
                        session_id, user.user_id
                    )
                if not belongs:
                    raise HTTPException(
                        status_code=403,
                        detail="无权访问此会话（不属于当前用户）",
                    )

        # 按时间升序排列（get_by_session 返回降序）
        records = list(reversed(records))

        messages = [
            MessageItem(
                id=str(r.id),
                role=r.role,
                content=r.content,
                intent=r.intent,
                created_at=r.created_at.isoformat(),
            )
            for r in records
        ]
    except Exception:
        logger.exception(f"Failed to load messages for session {session_id}")
        return SessionMessagesResponse(session_id=session_id, messages=[])

    return SessionMessagesResponse(session_id=session_id, messages=messages)


# ============================================================================
# 路由 — 会话元数据 & 清理
# ============================================================================

@api_router.get("/sessions/{session_id}", response_model=SessionInfo)
async def get_session(session_id: str, request: Request) -> SessionInfo:
    """
    获取指定对话会话的元数据（需要认证）。

    从 Redis（热缓存）查询会话消息计数。若 Redis 不可用则返回 0。
    """
    user = await get_current_user(request)
    logger.info(f"GET /sessions/{session_id} user={user.user_id}")

    # 验证 UUID 格式
    from uuid import UUID

    try:
        UUID(session_id)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid session_id format: {session_id!r}",
        )

    # 从 Redis 查询消息计数
    from app.storage.redis_client import get_redis_client

    redis_client = get_redis_client()
    message_count = 0
    if await redis_client.health_check():
        try:
            message_count = await redis_client.client.llen(
                f"chat_history:{session_id}"
            )
        except Exception:
            logger.warning(f"Failed to query Redis for session {session_id}")

    return SessionInfo(
        session_id=session_id,
        message_count=message_count or 0,
        created_at=None,
    )


@api_router.delete("/sessions/{session_id}", status_code=200)
async def clear_session(session_id: str, request: Request) -> dict[str, str]:
    """
    清除会话的对话上下文（需要认证）。

    从 Redis（热缓存）移除会话数据并从 PostgreSQL 删除记录。
    仅允许会话所有者执行此操作。
    """
    user = await get_current_user(request)

    from uuid import UUID

    try:
        UUID(session_id)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid session_id format: {session_id!r}",
        )

    logger.info(f"DELETE /sessions/{session_id} user={user.user_id}")

    # 从 Redis 删除热缓存数据
    from app.storage.redis_client import get_redis_client

    redis_client = get_redis_client()
    if await redis_client.health_check():
        try:
            await redis_client.delete_history(session_id)
            logger.debug(f"Deleted Redis history for session {session_id}")
        except Exception:
            logger.warning(f"Failed to delete Redis history for session {session_id}")

    # 从 PostgreSQL 删除记录（ChatHistoryRepository.delete_by_session）
    from app.storage.db import get_db_manager
    from app.storage.repositories import ChatHistoryRepository

    db = get_db_manager()
    if await db.health_check():
        try:
            async with db.session_context() as session:
                repo = ChatHistoryRepository(session)
                deleted = await repo.delete_by_session(session_id)
                logger.info(f"Deleted {deleted} PG records for session {session_id}")
        except Exception:
            logger.warning(f"Failed to delete PG records for session {session_id}")

    return {"status": "ok", "session_id": session_id}


# ============================================================================
# 路由 — 用户画像 / 长期记忆
# ============================================================================

@api_router.get("/profile", response_model=UserProfileResponse)
async def get_profile(request: Request) -> UserProfileResponse:
    """
    获取当前用户的完整画像数据（长期记忆）。

    包括：偏好设置、常关注电站、对话摘要、记忆笔记。
    """
    user = await get_current_user(request)

    from app.storage.db import get_db_manager
    from app.storage.repositories import UserProfileRepository

    db = get_db_manager()
    if not await db.health_check():
        return UserProfileResponse(user_id=str(user.user_id))

    try:
        async with db.session_context() as session:
            repo = UserProfileRepository(session)
            profile = await repo.get_or_create(user.user_id)
            return UserProfileResponse(
                user_id=str(profile.user_id),
                preferences=profile.preferences or {},
                frequent_stations=profile.frequent_stations or {},
                conversation_summary=profile.conversation_summary,
                memory_notes=list(profile.memory_notes or []),
                updated_at=profile.updated_at.isoformat() if profile.updated_at else None,
            )
    except Exception:
        logger.exception("Failed to load profile")
        return UserProfileResponse(user_id=str(user.user_id))


@api_router.put("/profile", response_model=UserProfileResponse)
async def update_profile(
    request: Request,
    body: UpdateProfileRequest,
) -> UserProfileResponse:
    """
    更新用户偏好设置和昵称。
    """
    user = await get_current_user(request)

    from app.storage.db import get_db_manager
    from app.storage.repositories import UserProfileRepository, UserRepository

    db = get_db_manager()
    if not await db.health_check():
        raise HTTPException(status_code=503, detail="服务暂时不可用")

    try:
        async with db.session_context() as session:
            # 更新昵称
            if body.name is not None:
                user_repo = UserRepository(session)
                db_user = await user_repo.get_by_id(user.user_id)
                if db_user:
                    db_user.name = body.name

            # 更新偏好
            profile = None
            if body.preferences is not None:
                profile_repo = UserProfileRepository(session)
                profile = await profile_repo.update_preferences(
                    user.user_id, body.preferences
                )
            else:
                profile_repo = UserProfileRepository(session)
                profile = await profile_repo.get_or_create(user.user_id)

            return UserProfileResponse(
                user_id=str(profile.user_id),
                preferences=profile.preferences or {},
                frequent_stations=profile.frequent_stations or {},
                conversation_summary=profile.conversation_summary,
                memory_notes=list(profile.memory_notes or []),
                updated_at=profile.updated_at.isoformat() if profile.updated_at else None,
            )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to update profile")
        raise HTTPException(status_code=500, detail="更新失败")


@api_router.post("/profile/memory", status_code=201)
async def add_memory_note(
    request: Request,
    body: AddMemoryNoteRequest,
) -> dict[str, str]:
    """
    添加一条长期记忆笔记。

    可由用户手动添加，也可由 Agent 在对话结束后自动提炼。
    """
    user = await get_current_user(request)

    from app.storage.db import get_db_manager
    from app.storage.repositories import UserProfileRepository

    db = get_db_manager()
    if not await db.health_check():
        raise HTTPException(status_code=503, detail="服务暂时不可用")

    try:
        async with db.session_context() as session:
            repo = UserProfileRepository(session)
            await repo.add_memory_note(user.user_id, body.content, body.source)
            logger.info(f"Memory note added for user={user.user_id}")
            return {"status": "ok"}
    except Exception:
        logger.exception("Failed to add memory note")
        raise HTTPException(status_code=500, detail="添加失败")


# ============================================================================
# RAG 管理端点
# ============================================================================

rag_router = APIRouter(tags=["RAG"], prefix="/rag")


class IngestDocumentsRequest(BaseModel):
    """文档导入请求。"""

    path: str = Field(
        ...,
        description="文件或目录的绝对路径",
        examples=["app/data/documents/inverter_manual.md"],
    )
    doc_type: str = Field(
        default="manual",
        description="文档类型: manual | fault_codes | procedure | ticket | other",
    )
    clear_existing: bool = Field(
        default=False,
        description="是否先清除同 source 的已有文档",
    )


class IngestDocumentsResponse(BaseModel):
    """文档导入响应。"""

    status: str
    files_processed: int
    chunks_ingested: int


@rag_router.post("/ingest", response_model=IngestDocumentsResponse)
async def ingest_documents(
    body: IngestDocumentsRequest,
) -> IngestDocumentsResponse:
    """
    向知识库导入文档。

    支持单个文件或整个目录。导入后自动分块、embedding 并存入 Chroma。

    调用方式:
        POST /api/v1/rag/ingest
        {
            "path": "app/data/documents/inverter_manual.md",
            "doc_type": "manual"
        }
    """
    from pathlib import Path

    from app.rag.document_loader import DocumentLoader

    loader = DocumentLoader()
    target = Path(body.path)

    if not target.exists():
        raise HTTPException(status_code=404, detail=f"路径不存在: {body.path}")

    try:
        if target.is_file():
            chunks = loader.load_file(str(target), doc_type=body.doc_type)
            files_processed = 1
        elif target.is_dir():
            chunks = loader.load_directory(str(target), doc_type=body.doc_type)
            files_processed = sum(
                1 for _ in target.rglob("*")
                if _.suffix.lower() in {".txt", ".md", ".pdf", ".csv"}
            )
        else:
            raise HTTPException(status_code=400, detail="路径不是文件也不是目录")

        chunks_ingested = loader.ingest_to_store(
            chunks, clear_existing=body.clear_existing
        )

        logger.info(
            f"RAG ingest: {files_processed} files → {chunks_ingested} chunks"
        )

        return IngestDocumentsResponse(
            status="ok",
            files_processed=files_processed,
            chunks_ingested=chunks_ingested,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"RAG ingest failed: {e}")
        raise HTTPException(status_code=500, detail=f"导入失败: {e}")


@rag_router.get("/stats")
async def get_rag_stats() -> dict[str, Any]:
    """
    获取 RAG 系统统计信息。

    返回知识库和长期记忆的文档/记忆数量。
    """
    from app.rag.vector_store import get_vector_store

    store = get_vector_store()
    return {
        "knowledge_base": store.get_kb_stats(),
        "memory": store.get_memory_stats(),
    }


@rag_router.get("/search")
async def search_knowledge_base(
    q: str = Query(..., description="检索查询", min_length=1),
    top_k: int = Query(default=5, ge=1, le=20, description="返回结果数"),
) -> dict[str, Any]:
    """
    在知识库中搜索相关文档。

    用于测试和调试 RAG 检索效果。
    """
    from app.rag.kb_retriever import KnowledgeBaseRetriever

    retriever = KnowledgeBaseRetriever()
    results = await retriever.retrieve(q, top_k=top_k)

    return {
        "query": q,
        "results": results,
    }


@rag_router.delete("/documents/{source}")
async def delete_document(source: str) -> dict[str, Any]:
    """
    按 source 名称删除知识库中的文档。

    Args:
        source: 文档来源名称（导入时的 title/source）。
    """
    from app.rag.vector_store import get_vector_store

    store = get_vector_store()
    deleted = store.delete_kb_document(source)

    return {
        "status": "ok",
        "source": source,
        "chunks_deleted": deleted,
    }
