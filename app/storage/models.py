"""
SQLAlchemy ORM models.

Defines the table mappings for:
    - ChatHistory: Full conversation record for audit trail and retrieval.
    - Checkpoint: LangGraph state snapshots for graph resume/replay.

All models use UUID primary keys and UTC timestamps.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# ============================================================================
# Declarative Base
# ============================================================================

class Base(DeclarativeBase):
    """Base class for all ORM models. Provides common metadata."""

    type_annotation_map = {
        uuid.UUID: PG_UUID(as_uuid=True),
    }


# ============================================================================
# ChatHistory — full conversation log
# ============================================================================

class ChatHistory(Base):
    """
    Stores every message in every conversation for audit, retrieval, and analytics.

    Each row represents a single message (user, assistant, system, or tool).
    Messages are grouped by session_id and ordered by created_at.
    """

    __tablename__ = "chat_history"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="Unique message identifier",
    )
    session_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="Conversation session identifier",
    )
    station_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="Associated power station identifier (if any)",
    )
    tenant_id: Mapped[str] = mapped_column(
        String(128),
        default="default",
        comment="Multi-tenant identifier",
    )
    role: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="Message role: user, assistant, system, or tool",
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Full message content (text or serialized tool result)",
    )
    intent: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="Classified intent label for this turn (if applicable)",
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        comment="Arbitrary metadata (model, tokens, latency, etc.)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        comment="UTC timestamp of message creation",
    )

    # Composite indexes for common queries
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
# Checkpoint — LangGraph state persistence
# ============================================================================

class Checkpoint(Base):
    """
    Stores LangGraph state checkpoints for agent graph resume and replay.

    Each checkpoint captures the full AgentState at a specific point in the
    graph execution. Checkpoints are linked via parent_checkpoint_id to form
    a version chain within each thread.
    """

    __tablename__ = "checkpoints"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="Unique checkpoint identifier",
    )
    thread_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
        comment="LangGraph thread identifier (corresponds to session_id)",
    )
    checkpoint_ns: Mapped[str] = mapped_column(
        String(256),
        default="",
        comment="Checkpoint namespace for sub-graphs",
    )
    checkpoint_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        comment="Unique checkpoint ID within the thread",
    )
    parent_checkpoint_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        comment="Parent checkpoint ID for chain traversal",
    )
    type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Serialization type (e.g., 'json')",
    )
    checkpoint: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        comment="Full serialized AgentState snapshot",
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        comment="Checkpoint metadata (source, step, etc.)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        comment="UTC timestamp of checkpoint creation",
    )

    __table_args__ = (
        # Unique: one checkpoint per thread + namespace + checkpoint_id
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
# CheckpointWrites — LangGraph pending writes
# ============================================================================

class CheckpointWrite(Base):
    """
    Stores pending writes for LangGraph checkpoints.

    Each row captures a write operation that is part of a checkpoint.
    Used by LangGraph's checkpointing system for resumable execution.
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
