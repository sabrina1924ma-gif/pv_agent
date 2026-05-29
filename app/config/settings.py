"""
Centralized application settings using Pydantic-settings.

Reads from .env file and environment variables. All configuration flows
through this module — no other module should read from os.environ directly.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide configuration. All values can be overridden via env vars."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---
    app_name: str = "pv_agent"
    app_version: str = "0.1.0"
    debug: bool = False
    log_level: Literal["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"] = (
        "INFO"
    )
    environment: Literal["production", "staging", "development"] = "production"

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 4

    # --- Security ---
    secret_key: str = secrets.token_hex(32)
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60

    # --- Redis ---
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""
    redis_session_ttl: int = 86400       # 24 hours
    redis_tool_cache_ttl: int = 300      # 5 minutes

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
    # OpenAI-compatible endpoint (no trailing slash)
    deepseek_base_url: str = "https://api.deepseek.com"
    # deepseek-v4-pro (旗舰, 1.6T/49B MoE) or deepseek-v4-flash (高性价比, 284B/13B MoE)
    deepseek_model: str = "deepseek-v4-flash"
    # Lightweight tasks like intent classification use the same model with temp=0
    deepseek_light_model: str = "deepseek-v4-flash"
    llm_temperature: float = 0.3
    llm_max_tokens: int = 4096
    llm_timeout_seconds: int = 120

    # --- LangGraph ---
    langgraph_max_recursion: int = 25
    langgraph_checkpoint_persist: bool = True
    langgraph_checkpoint_retention_days: int = 30

    @property
    def redis_url(self) -> str:
        """Build the Redis connection URL."""
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def postgres_url(self) -> str:
        """Build the async PostgreSQL connection URL."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_sync_url(self) -> str:
        """Build the synchronous PostgreSQL connection URL (for Alembic, etc.)."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    """Return the cached singleton Settings instance."""
    return Settings()
