"""
REST API 路由定义。

所有路由均以 /api/v1 为前缀。请求流经 Agent 图，
由 Agent 图协调意图分类、工具调用和报告生成。

HTTP 上下文：graph.ainvoke() 在 FastAPI 的事件循环中运行。
LangGraph 节点内的异步操作（LLM 调用、工具 I/O）通过 asyncio
协作调度正确参与同一事件循环。

端点：
    POST   /api/v1/chat               — 非流式聊天，返回完整响应
    GET    /api/v1/sessions/{id}      — 获取会话元数据
    DELETE /api/v1/sessions/{id}      — 清除会话上下文
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app.agent.graph import get_graph
from app.agent.schema import AgentState
from app.utils.pdf import markdown_to_pdf
from loguru import logger

api_router = APIRouter(tags=["Agent"])


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


class PdfReportRequest(BaseModel):
    """PDF 报告生成请求。"""

    markdown: str = Field(
        ...,
        min_length=1,
        max_length=100_000,
        description="Markdown 格式的报告文本",
    )
    title: str = Field(
        default="光伏电站报告",
        max_length=200,
        description="PDF 文档标题",
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


# ============================================================================
# 路由
# ============================================================================

@api_router.post("/chat", response_model=ChatResponse, status_code=200)
async def chat(request: Request, body: ChatRequest) -> ChatResponse:
    """
    非流式聊天端点。

    通过完整的 LangGraph Agent 管线处理用户消息并同步返回完整响应。

    管线：load_history → intent_router → (tool_executor?) → report_node → respond

    图仅编译一次并被缓存（参见 get_graph()），因此重复调用
    复用同一个图实例。除非配置了 LangGraph 检查点，
    否则调用间不会持久化状态。
    """
    session_id: str = getattr(request.state, "session_id", "unknown")
    request_id: str = getattr(request.state, "request_id", "unknown")

    logger.info(
        f"POST /chat session={session_id} station={body.station_id} "
        f"msg_len={len(body.message)}"
    )

    # ------------------------------------------------------------------
    # 1. 构建初始图状态
    # ------------------------------------------------------------------
    initial_state: AgentState = {
        "messages": [HumanMessage(content=body.message)],
        "session_id": session_id,
        "station_id": body.station_id,
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
        # 截断报告用于摘要字段——完整报告在 report_md 中
        first_line = report_md.split("\n", 1)[0] if report_md else ""
        summary = first_line[:200] if first_line else "报告已生成"
    else:
        summary = f"已处理您的请求 (意图: {intent or '未分类'})"

    return ChatResponse(
        session_id=session_id,
        message=summary,
        intent=intent,
        report_md=report_md,
        tool_results=tool_results,
        error=error,
    )


@api_router.get("/sessions/{session_id}", response_model=SessionInfo)
async def get_session(session_id: str, request: Request) -> SessionInfo:
    """
    获取指定对话会话的元数据。

    从 Redis（热缓存）查询会话消息计数。若 Redis 不可用则返回 0。
    """
    logger.info(f"GET /sessions/{session_id}")

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
    清除会话的对话上下文。

    从 Redis（热缓存）移除会话数据并从 PostgreSQL 删除记录。
    """
    from uuid import UUID

    try:
        UUID(session_id)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid session_id format: {session_id!r}",
        )

    logger.info(f"DELETE /sessions/{session_id}")

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
# PDF 报告生成
# ============================================================================

@api_router.post("/report/pdf", status_code=200)
async def generate_pdf_report(
    request: Request,
    body: PdfReportRequest,
):
    """
    将 Markdown 报告转换为可下载的 PDF 文件。

    接收 Markdown 格式的报告文本，将其渲染为带样式的 PDF
    （A4 纸张、中文字体），并通过 HTTP 响应直接返回文件字节流。

    返回：
        Content-Type: application/pdf
        Content-Disposition: attachment; filename="report.pdf"
    """
    from fastapi.responses import Response

    session_id: str = getattr(request.state, "session_id", "unknown")
    logger.info(
        f"POST /report/pdf session={session_id} "
        f"title={body.title!r} md_len={len(body.markdown)}"
    )

    try:
        pdf_bytes = await markdown_to_pdf(
            markdown_text=body.markdown,
            title=body.title,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        logger.exception("PDF generation failed")
        raise HTTPException(status_code=500, detail=str(exc))

    # 文件名安全处理：移除可能破坏文件名的字符
    safe_title = "".join(
        c for c in body.title if c.isalnum() or c in (" ", "-", "_")
    ).strip() or "report"
    filename = f"{safe_title}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )
