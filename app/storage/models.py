"""
SQLAlchemy ORM 模型。

定义了以下表的映射:
    - ChatHistory: 完整对话记录，用于审计追踪和检索。
    - Checkpoint: LangGraph 状态快照，用于图表恢复/重放。

所有模型使用 UUID 主键和 UTC 时间戳。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# ============================================================================
# 声明式基类
# ============================================================================

class Base(DeclarativeBase):
    """所有 ORM 模型的基类。提供公共元数据。"""

    type_annotation_map = {
        uuid.UUID: PG_UUID(as_uuid=True),
    }


# ============================================================================
# ChatHistory — 完整对话日志
# ============================================================================

class ChatHistory(Base):
    """
    存储每条消息的完整对话记录，用于审计、检索和分析。

    每行代表一条消息（用户、助手、系统或工具）。
    消息按 session_id 分组，按 created_at 排序。
    """

    __tablename__ = "chat_history"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="消息唯一标识符",
    )
    session_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="会话标识符",
    )
    station_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="关联的电站标识符（如有）",
    )
    tenant_id: Mapped[str] = mapped_column(
        String(128),
        default="default",
        comment="多租户标识符",
    )
    role: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="消息角色：user、assistant、system 或 tool",
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="完整消息内容（文本或序列化的工具结果）",
    )
    intent: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="本轮对话的分类意图标签（如适用）",
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        comment="任意元数据（模型、Token 数、延迟等）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        comment="消息创建的 UTC 时间戳",
    )

    # 常用查询的复合索引
    __table_args__ = (
        Index("ix_chat_history_session_created", "session_id", "created_at"),
        Index("ix_chat_history_tenant", "tenant_id"),
        Index("ix_chat_history_station", "station_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<ChatHistory(id={self.id}, session={self.session_id}, "
            f"role={self.role}, intent={self.intent})>"
        )


# ============================================================================
# Checkpoint — LangGraph 状态持久化
# ============================================================================

class Checkpoint(Base):
    """
    存储 LangGraph 状态检查点，用于 agent 图恢复和重放。

    每个检查点捕获图执行过程中特定时刻的完整 AgentState。
    检查点通过 parent_checkpoint_id 链接，形成每个线程内的版本链。
    """

    __tablename__ = "checkpoints"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="检查点唯一标识符",
    )
    thread_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
        comment="LangGraph 线程标识符（对应 session_id）",
    )
    checkpoint_ns: Mapped[str] = mapped_column(
        String(256),
        default="",
        comment="子图的检查点命名空间",
    )
    checkpoint_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="线程内的唯一检查点 ID",
    )
    parent_checkpoint_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="父检查点 ID，用于链式遍历",
    )
    type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="序列化类型（如 'json'）",
    )
    checkpoint: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        comment="完整的序列化 AgentState 快照",
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        comment="检查点元数据（来源、步骤等）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        comment="检查点创建的 UTC 时间戳",
    )

    __table_args__ = (
        # 唯一约束：每个线程 + 命名空间 + checkpoint_id 只有一个检查点
        Index(
            "ix_checkpoints_thread_checkpoint",
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
            unique=True,
        ),
        Index("ix_checkpoints_thread_created", "thread_id", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<Checkpoint(id={self.id}, thread={self.thread_id}, "
            f"checkpoint_id={self.checkpoint_id})>"
        )


# ============================================================================
# CheckpointWrite — LangGraph 待写入数据
# ============================================================================

class CheckpointWrite(Base):
    """
    存储 LangGraph 检查点的待写入数据。

    每行捕获作为检查点一部分的写入操作。
    由 LangGraph 的检查点系统用于可恢复执行。
    """

    __tablename__ = "checkpoint_writes"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    thread_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    checkpoint_ns: Mapped[str] = mapped_column(
        String(256), default=""
    )
    checkpoint_id: Mapped[str] = mapped_column(
        String(128), nullable=False
    )
    task_id: Mapped[str] = mapped_column(
        String(128), nullable=False
    )
    task_path: Mapped[str] = mapped_column(
        String(512), default=""
    )
    idx: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    channel: Mapped[str] = mapped_column(
        String(128), nullable=False
    )
    type: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    value: Mapped[dict] = mapped_column(
        JSONB, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    __table_args__ = (
        Index(
            "ix_checkpoint_writes_thread",
            "thread_id",
            "checkpoint_ns",
            "checkpoint_id",
        ),
        Index("ix_checkpoint_writes_task", "thread_id", "task_id"),
    )
