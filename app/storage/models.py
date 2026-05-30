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

# ============================================================================
# User — 用户账户
# ============================================================================

class User(Base):
    """
    用户账户，通过用户名+密码登录。

    手机号为选填字段，用于后续可能的 SMS 通知或 2FA。
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="用户唯一标识符",
    )
    username: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        comment="登录用户名",
    )
    hashed_password: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        comment="PBKDF2-SHA256 哈希后的密码",
    )
    phone: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        unique=True,
        index=True,
        comment="手机号（选填）",
    )
    name: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="用户昵称",
    )
    avatar: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        comment="头像 URL",
    )
    is_active: Mapped[bool] = mapped_column(
        default=True,
        comment="是否启用",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        comment="注册时间",
    )
    last_login: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="最后登录时间",
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, username={self.username})>"


# ============================================================================
# VerificationCode — 短信验证码
# ============================================================================

class VerificationCode(Base):
    """
    手机验证码（备用，当前未启用）。

    TTL 由调用方传入，通常 5 分钟。
    """

    __tablename__ = "verification_codes"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    phone: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="目标手机号",
    )
    code: Mapped[str] = mapped_column(
        String(6),
        nullable=False,
        comment="6 位验证码",
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="过期时间",
    )
    used: Mapped[bool] = mapped_column(
        default=False,
        comment="是否已被使用",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_verification_codes_phone", "phone", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<VerificationCode(phone={self.phone}, used={self.used})>"


# ============================================================================
# UserProfile — 用户画像 / 长期记忆
# ============================================================================

class UserProfile(Base):
    """
    用户画像，存储长期记忆和偏好。

    字段：
        preferences: 用户偏好设置（通知、语言等）
        frequent_stations: 常关注的电站列表及其关注度
        conversation_summary: Agent 从对话中提炼的用户关注点摘要
        memory_notes: Agent 记录的关于用户的长期记忆笔记
    """

    __tablename__ = "user_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        unique=True,
        index=True,
        comment="关联的用户 ID",
    )
    preferences: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        comment="用户偏好设置",
    )
    frequent_stations: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        comment="常关注电站及其权重",
    )
    conversation_summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Agent 提炼的对话关注点摘要",
    )
    memory_notes: Mapped[dict] = mapped_column(
        JSONB,
        default=list,
        comment="长期记忆笔记列表 [{content, source, created_at}]",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"<UserProfile(user_id={self.user_id})>"


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
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
        index=True,
        comment="关联的用户 ID（用户隔离）",
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
        Index("ix_chat_history_user_session", "user_id", "session_id"),
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
