"""存储层: Redis 缓存、PostgreSQL 持久化、ORM 模型、数据访问层。

三层数据架构:
    Redis (热, TTL 24h/5min):
        - 会话聊天历史 (List, 实时追加)
        - 工具调用结果缓存 (String, 短期去重)
        - 已生成报告缓存 (String, 热读取)

    PostgreSQL (冷, 持久化):
        - ChatHistory: 全量聊天记录，审计 + 检索
        - Checkpoint: LangGraph 状态快照，断点续传
        - CheckpointWrite: 快照中间写入记录

    Repository (抽象层):
        - 封装 CRUD，agent node 不直接写 SQL
        - 双写 (Redis + PG) 在 save_turn() 中完成
"""

from app.storage.redis_client import RedisClient, get_redis_client
from app.storage.db import DatabaseManager, get_db_manager
from app.storage.models import Base, ChatHistory, Checkpoint, CheckpointWrite
from app.storage.repositories import ChatHistoryRepository, CheckpointRepository

__all__ = [
    # Redis
    "RedisClient",
    "get_redis_client",
    # PostgreSQL
    "DatabaseManager",
    "get_db_manager",
    # ORM Models
    "Base",
    "ChatHistory",
    "Checkpoint",
    "CheckpointWrite",
    # Repositories
    "ChatHistoryRepository",
    "CheckpointRepository",
]
