"""
集中化应用设置，使用 Pydantic-settings。

从 .env 文件和环境变量读取。所有配置都通过此模块流转——
其他模块不应直接读取 os.environ。
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用全局配置。所有值均可通过环境变量覆盖。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- 应用 ---
    app_name: str = "pv_agent"
    app_version: str = "0.1.0"
    debug: bool = False
    log_level: Literal["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"] = (
        "INFO"
    )
    environment: Literal["production", "staging", "development"] = "production"

    # --- 服务器 ---
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 4

    # --- 安全 ---
    secret_key: str = secrets.token_hex(32)
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60

    # --- Auth / JWT ---
    jwt_secret: str = secrets.token_hex(32)
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 43200  # 30 days

    # --- Admin ---
    admin_username: str = "pvagent"
    admin_password: str = "123456"

    # --- SMS (Alibaba Cloud) — 备用，当前未启用 ---
    sms_provider: Literal["aliyun", "dev"] = "dev"
    aliyun_access_key_id: str = ""
    aliyun_access_key_secret: str = ""
    aliyun_sms_sign_name: str = "PV Agent"
    aliyun_sms_template_code: str = "SMS_123456789"

    # --- Redis ---
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""
    redis_session_ttl: int = 86400       # 24 小时
    redis_tool_cache_ttl: int = 300      # 5 分钟

    # --- PostgreSQL ---
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "sabrina"
    postgres_password: str = "1234"
    postgres_db: str = "pv_agent"
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_timeout: int = 30
    db_echo: bool = False

    # --- DeepSeek API ---
    deepseek_api_key: str = "sk-your-api-key-here"
    # OpenAI 兼容接口（无尾随斜杠）
    deepseek_base_url: str = "https://api.deepseek.com"
    # deepseek-v4-pro（旗舰，1.6T/49B MoE）或 deepseek-v4-flash（高性价比，284B/13B MoE）
    deepseek_model: str = "deepseek-v4-flash"
    # 意图分类等轻量任务使用相同模型，temp=0
    deepseek_light_model: str = "deepseek-v4-flash"
    llm_temperature: float = 0.3
    llm_max_tokens: int = 4096
    llm_timeout_seconds: int = 120

    # --- LangGraph ---
    langgraph_max_recursion: int = 25
    langgraph_checkpoint_persist: bool = True
    langgraph_checkpoint_retention_days: int = 30

    # --- RAG (Retrieval-Augmented Generation) ---
    # Embedding 模型：BAAI/bge-small-zh-v1.5（中文语义，384维，~100MB）
    rag_embedding_model: str = "BAAI/bge-small-zh-v1.5"
    rag_embedding_device: str = "cpu"  # cpu | cuda
    # Chroma 持久化路径（相对于项目根目录）
    rag_chroma_persist_dir: str = "chroma_db"
    # 检索配置
    rag_kb_top_k: int = 5        # 知识库每次检索返回的文档片段数
    rag_memory_top_k: int = 3    # 长期记忆每次检索返回的历史会话数
    rag_min_similarity: float = 0.3  # 最低相似度阈值，低于此值的结果丢弃
    # 文档分块配置
    rag_chunk_size: int = 500       # 每个文本块的最大字符数
    rag_chunk_overlap: int = 80     # 相邻块之间的重叠字符数
    # 长期记忆配置
    rag_memory_summary_enabled: bool = True  # 是否在每轮对话后生成摘要并索引

    @property
    def redis_url(self) -> str:
        """构建 Redis 连接 URL。"""
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def postgres_url(self) -> str:
        """构建异步 PostgreSQL 连接 URL。"""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_sync_url(self) -> str:
        """构建同步 PostgreSQL 连接 URL（用于 Alembic 等）。"""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def rag_chroma_path(self) -> str:
        """返回 Chroma 持久化目录的绝对路径。"""
        if Path(self.rag_chroma_persist_dir).is_absolute():
            return self.rag_chroma_persist_dir
        # 相对于项目根目录（app/config/ 的上两级）
        root = Path(__file__).resolve().parent.parent.parent
        return str(root / self.rag_chroma_persist_dir)


@lru_cache
def get_settings() -> Settings:
    """返回缓存的单例 Settings 实例。"""
    return Settings()
