"""
数据访问层（Repository 模式）。

每个 Repository 封装对一张表的 CRUD 操作，接收 AsyncSession 作为构造参数。
Session 的生命周期由调用方控制（FastAPI Depends 或 agent node 手动管理）。

分层原因:
    - agent node 不应该写原生 SQL：更换数据库或 ORM 时只需修改这一层
    - 业务逻辑与存储细节解耦：intent_router 只管"存一条消息"，不关心是 PostgreSQL 还是 Redis
    - 测试友好：注入 Mock Repository 即可对 agent 逻辑进行单元测试

设计决策:
    - save_turn() 同时写 Redis + PostgreSQL：热数据双写，读优先从 Redis 取
    - flush() 而非 commit()：session 的 commit 由上层 DatabaseManager 统一管理
    - 所有方法都是 async：数据库 I/O 不阻塞事件循环
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.models import ChatHistory, Checkpoint
from loguru import logger


# ============================================================================
# ChatHistoryRepository — 聊天记录持久化
# ============================================================================

class ChatHistoryRepository:
    """
    聊天消息的持久化操作。

    两个数据源:
        1. Redis（热缓存）：最近 24 小时的消息，用于 agent graph 的 load_history 节点
        2. PostgreSQL（冷存储）：全量消息，用于审计、分析和长期检索

    典型调用链:
        用户发消息 → router 收到请求
          → repo.save_message(role="user", ...)
          → agent 跑完 graph
          → repo.save_message(role="assistant", ...)
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        station_id: str | None = None,
        tenant_id: str = "default",
        intent: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ChatHistory:
        """
        持久化一条消息到 PostgreSQL。

        Args:
            session_id: 会话 ID。
            role: user / assistant / system / tool。
            content: 消息全文（含序列化的 tool result）。
            station_id: 关联电站（可选）。
            tenant_id: 租户标识。
            intent: 此轮对话的意图标签。
            metadata: 附加信息（token 数、延迟、模型名等）。

        Returns:
            已持久化的 ChatHistory 记录。
        """
        record = ChatHistory(
            session_id=session_id,
            station_id=station_id,
            tenant_id=tenant_id,
            role=role,
            content=content,
            intent=intent,
            metadata_=metadata or {},
        )
        self._session.add(record)
        await self._session.flush()
        logger.debug(f"ChatHistory saved: id={record.id}, session={session_id}, role={role}")
        return record

    async def save_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
        *,
        station_id: str | None = None,
        intent: str | None = None,
        metadata: dict[str, Any] | None = None,
        redis_client: Any | None = None,
    ) -> tuple[ChatHistory, ChatHistory]:
        """
        保存一轮完整对话（user + assistant），同时写 Redis 热缓存。

        这是一次业务操作中最高频的方法 —— 每次用户提问都走这里。

        Args:
            session_id: 会话 ID。
            user_message: 用户原始输入。
            assistant_message: Agent 完整回复（含 Markdown 报告）。
            station_id: 关联电站。
            intent: 分类意图。
            metadata: 附加信息。
            redis_client: 可选 RedisClient 实例，传入则同步写 Redis。

        Returns:
            (user_record, assistant_record) 两条 ChatHistory 记录。
        """
        # 写 PostgreSQL（冷，持久化）
        user_record = await self.save_message(
            session_id=session_id,
            role="user",
            content=user_message,
            station_id=station_id,
            intent=intent,
            metadata=metadata,
        )

        assistant_record = await self.save_message(
            session_id=session_id,
            role="assistant",
            content=assistant_message,
            station_id=station_id,
            intent=intent,
            metadata=metadata,
        )

        # 双写 Redis 热缓存
        if redis_client is not None:
            await redis_client.save_message(
                session_id, "user", user_message,
                station_id=station_id, intent=intent,
            )
            await redis_client.save_message(
                session_id, "assistant", assistant_message,
                station_id=station_id, intent=intent,
            )

        return user_record, assistant_record

    async def save_tool_result(
        self,
        session_id: str,
        tool_name: str,
        tool_result: dict[str, Any],
        *,
        station_id: str | None = None,
    ) -> ChatHistory:
        """
        保存一条工具调用结果（role=tool）。

        content 字段存储序列化后的 JSON，方便后续 audit 和 replay。
        """
        content = json.dumps(tool_result, ensure_ascii=False)
        return await self.save_message(
            session_id=session_id,
            role="tool",
            content=content,
            station_id=station_id,
            metadata={"tool_name": tool_name},
        )

    async def get_by_session(
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[ChatHistory]:
        """
        按会话查询消息，返回最新在前。

        Args:
            session_id: 会话 ID。
            limit: 每页条数。
            offset: 偏移量。

        Returns:
            ChatHistory 列表，按 created_at DESC 排序。
        """
        stmt = (
            select(ChatHistory)
            .where(ChatHistory.session_id == session_id)
            .order_by(ChatHistory.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def get_by_intent(
        self,
        intent: str,
        limit: int = 50,
    ) -> Sequence[ChatHistory]:
        """
        按意图标签查询消息（用于分析"用户都问了什么类型的问题"）。

        Args:
            intent: 意图标签。
            limit: 返回上限。

        Returns:
            匹配的 ChatHistory 列表。
        """
        stmt = (
            select(ChatHistory)
            .where(ChatHistory.intent == intent)
            .order_by(ChatHistory.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def count_by_session(self, session_id: str) -> int:
        """返回某会话的消息总数。"""
        stmt = (
            select(func.count())
            .select_from(ChatHistory)
            .where(ChatHistory.session_id == session_id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def delete_by_session(self, session_id: str) -> int:
        """
        删除某会话的全部消息（GDPR 合规 / 用户清空历史）。

        Returns:
            删除的行数。
        """
        stmt = delete(ChatHistory).where(ChatHistory.session_id == session_id)
        result = await self._session.execute(stmt)
        await self._session.flush()
        logger.info(f"Deleted {result.rowcount} messages for session {session_id}")
        return result.rowcount

    async def delete_old_messages(self, days: int = 90) -> int:
        """
        清理 N 天前的旧消息（定时任务调用，控制存储成本）。

        Args:
            days: 保留天数，默认 90。

        Returns:
            删除的行数。
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = delete(ChatHistory).where(ChatHistory.created_at < cutoff)
        result = await self._session.execute(stmt)
        await self._session.flush()
        logger.info(f"Purged {result.rowcount} messages older than {days} days")
        return result.rowcount


# ============================================================================
# CheckpointRepository — LangGraph 状态快照
# ============================================================================

class CheckpointRepository:
    """
    LangGraph checkpoint 的持久化操作。

    每次 agent graph 执行到一个节点时，LangGraph 可以自动保存当前状态快照。
    这对以下场景至关重要:
        - 长时间执行的 agent 任务中断后恢复
        - 用户 "上一步" / "重试" 操作
        - 调试：回放某个节点的状态看当时 agent 在想什么

    注意: 这个 Repository 通常不直接调用，而是由 LangGraph 的 SqliteSaver
    或自定义 PostgresSaver 在内部使用。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(
        self,
        thread_id: str,
        checkpoint_id: str,
        checkpoint_data: dict[str, Any],
        *,
        checkpoint_ns: str = "",
        parent_checkpoint_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Checkpoint:
        """
        持久化一个状态快照。

        Args:
            thread_id: LangGraph thread ID（对应 session_id）。
            checkpoint_id: 快照唯一 ID（由 LangGraph 生成）。
            checkpoint_data: 完整的 AgentState 序列化结果。
            checkpoint_ns: 子图命名空间。
            parent_checkpoint_id: 上一个快照 ID，形成版本链。
            metadata: 附加元数据（当前节点、步数等）。

        Returns:
            已持久化的 Checkpoint 记录。
        """
        record = Checkpoint(
            thread_id=thread_id,
            checkpoint_ns=checkpoint_ns,
            checkpoint_id=checkpoint_id,
            parent_checkpoint_id=parent_checkpoint_id,
            type="json",
            checkpoint=checkpoint_data,
            metadata_=metadata or {},
        )
        self._session.add(record)
        await self._session.flush()
        logger.debug(
            f"Checkpoint saved: thread={thread_id}, checkpoint_id={checkpoint_id}"
        )
        return record

    async def get_latest(
        self,
        thread_id: str,
        checkpoint_ns: str = "",
    ) -> Checkpoint | None:
        """
        获取某线程的最新快照（用于恢复执行）。

        Returns:
            最新 Checkpoint，无记录则 None。
        """
        stmt = (
            select(Checkpoint)
            .where(
                Checkpoint.thread_id == thread_id,
                Checkpoint.checkpoint_ns == checkpoint_ns,
            )
            .order_by(Checkpoint.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(
        self,
        thread_id: str,
        checkpoint_id: str,
        checkpoint_ns: str = "",
    ) -> Checkpoint | None:
        """
        获取指定 ID 的快照（用于回退到历史状态）。

        Returns:
            指定 Checkpoint，不存在则 None。
        """
        stmt = select(Checkpoint).where(
            Checkpoint.thread_id == thread_id,
            Checkpoint.checkpoint_ns == checkpoint_ns,
            Checkpoint.checkpoint_id == checkpoint_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_thread(
        self,
        thread_id: str,
        limit: int = 20,
    ) -> Sequence[Checkpoint]:
        """
        列出某线程的所有快照，最新在前。

        用于调试面板展示 agent 执行历史。
        """
        stmt = (
            select(Checkpoint)
            .where(Checkpoint.thread_id == thread_id)
            .order_by(Checkpoint.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def count_by_thread(self, thread_id: str) -> int:
        """返回某线程的快照总数。"""
        stmt = (
            select(func.count())
            .select_from(Checkpoint)
            .where(Checkpoint.thread_id == thread_id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def delete_old(
        self,
        thread_id: str,
        retention_days: int = 30,
    ) -> int:
        """
        清理超过保留期的旧快照。

        每天可能会有多次 checkpoint 写入（每个节点一次），不清理会快速膨胀。
        建议通过定时任务每天调用一次。

        Args:
            thread_id: 线程 ID。
            retention_days: 保留天数。

        Returns:
            删除的行数。
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        stmt = delete(Checkpoint).where(
            Checkpoint.thread_id == thread_id,
            Checkpoint.created_at < cutoff,
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        logger.info(
            f"Purged {result.rowcount} checkpoints for thread {thread_id} "
            f"(older than {retention_days} days)"
        )
        return result.rowcount
