-- ============================================================
-- PV Agent - PostgreSQL Initialization Script
-- ============================================================

-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- ============================================================
-- Chat History Table
-- Stores every interaction for audit and context retrieval
-- ============================================================
CREATE TABLE IF NOT EXISTS chat_history (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id      VARCHAR(64) NOT NULL,
    station_id      VARCHAR(128),
    tenant_id       VARCHAR(128) DEFAULT 'default',
    role            VARCHAR(32) NOT NULL,  -- 'user', 'assistant', 'system', 'tool'
    content         TEXT NOT NULL,
    intent          VARCHAR(64),            -- classified intent label
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for chat_history
CREATE INDEX IF NOT EXISTS idx_chat_history_session_id
    ON chat_history (session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_history_tenant_id
    ON chat_history (tenant_id);
CREATE INDEX IF NOT EXISTS idx_chat_history_station_id
    ON chat_history (station_id);
CREATE INDEX IF NOT EXISTS idx_chat_history_created_at
    ON chat_history (created_at DESC);

-- ============================================================
-- LangGraph Checkpoints Table
-- Persists agent state snapshots for resume/replay
-- ============================================================
CREATE TABLE IF NOT EXISTS checkpoints (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    thread_id       VARCHAR(128) NOT NULL,   -- LangGraph thread identifier
    checkpoint_ns   VARCHAR(256) DEFAULT '', -- checkpoint namespace
    checkpoint_id   VARCHAR(128) NOT NULL,   -- unique within thread
    parent_checkpoint_id VARCHAR(128),       -- for checkpoint chain
    type            VARCHAR(64) NOT NULL,    -- serialization type
    checkpoint      JSONB NOT NULL,          -- full state snapshot
    metadata        JSONB DEFAULT '{}',      -- arbitrary metadata
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Unique constraint per thread+namespace+checkpoint
CREATE UNIQUE INDEX IF NOT EXISTS idx_checkpoints_thread_checkpoint
    ON checkpoints (thread_id, checkpoint_ns, checkpoint_id);
CREATE INDEX IF NOT EXISTS idx_checkpoints_thread_id
    ON checkpoints (thread_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_checkpoints_created_at
    ON checkpoints (created_at DESC);

-- ============================================================
-- Writes Table (for LangGraph checkpoint writes)
-- ============================================================
CREATE TABLE IF NOT EXISTS checkpoint_writes (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    thread_id       VARCHAR(128) NOT NULL,
    checkpoint_ns   VARCHAR(256) DEFAULT '',
    checkpoint_id   VARCHAR(128) NOT NULL,
    task_id         VARCHAR(128) NOT NULL,
    task_path       VARCHAR(512) DEFAULT '',
    idx             INTEGER NOT NULL,
    channel         VARCHAR(128) NOT NULL,
    type            VARCHAR(64),
    value           JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_checkpoint_writes_thread
    ON checkpoint_writes (thread_id, checkpoint_ns, checkpoint_id);
CREATE INDEX IF NOT EXISTS idx_checkpoint_writes_task
    ON checkpoint_writes (thread_id, task_id);
