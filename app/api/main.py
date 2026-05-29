"""
FastAPI 应用程序入口点。

协调应用的创建：配置中间件、挂载路由器、
在启动时初始化 Agent 图和连接池，并在退出时优雅关闭。

中间件执行顺序（请求从内到外）：
    1. RateLimitMiddleware      — 最内层：按 IP 限制请求频率
    2. RequestIDMiddleware       — 注入 X-Request-Id 用于链路追踪
    3. SessionMiddleware         — 分配 / 验证 session_id
    4. SecurityHeadersMiddleware — 防御性 HTTP 头
    5. CORSMiddleware            — 最外层：处理预检 / 跨域请求
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.middleware import (
    RateLimitMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
    SessionMiddleware,
)
from app.api.router import api_router
from app.api.websocket import websocket_endpoint
from app.agent.graph import get_graph
from app.config import get_settings
from loguru import logger


# ---------------------------------------------------------------------------
# 应用生命周期（启动 / 关闭钩子）
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """
    管理应用生命周期。

    启动时：
        - 编译并缓存 LangGraph 状态图。
        - 预热 Redis 和 PostgreSQL 连接池。

    关闭时：
        - 优雅关闭连接池。
        - 释放所有剩余资源。
    """
    settings = get_settings()
    logger.info(
        f"Starting {settings.app_name} v{settings.app_version} "
        f"({settings.environment} mode)"
    )

    # --- 启动：编译 Agent 图 ---
    logger.info("Compiling agent graph...")
    application.state.graph = get_graph()
    logger.info("Agent graph ready")

    # --- 启动：预热数据库连接池 ---
    try:
        from app.storage.db import get_db_manager
        db = get_db_manager()
        await db.connect()
        application.state.db_engine = db.engine
        logger.info("Database engine initialized")
    except Exception:
        logger.warning("Database not available — running without persistence")
        application.state.db_engine = None

    # --- 启动：验证 Redis 连接 ---
    try:
        from app.storage.redis_client import get_redis_client
        redis = get_redis_client()
        await redis.connect()
        application.state.redis_client = redis
        logger.info("Redis connection verified")
    except Exception:
        logger.warning("Redis not available — session persistence disabled")
        application.state.redis_client = None

    yield

    # --- 关闭 ---
    logger.info("Shutting down...")

    # 释放数据库引擎
    engine = getattr(application.state, "db_engine", None)
    if engine is not None:
        await engine.dispose()
        logger.info("Database engine disposed")

    # 断开 Redis 连接
    redis = getattr(application.state, "redis_client", None)
    if redis is not None:
        try:
            await redis.disconnect()
            logger.info("Redis connection closed")
        except Exception:
            pass

    logger.info("Shutdown complete")


# ---------------------------------------------------------------------------
# 应用工厂
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """
    构建并配置 FastAPI 应用程序。

    中间件按内层优先添加（最后注册的为最外层）。
    执行顺序请参考模块文档字符串。

    返回：
        完全配置好的 FastAPI 应用实例。
    """
    settings = get_settings()

    app = FastAPI(
        title="PV Agent",
        description=(
            "AI Agent system for photovoltaic power/equipment data query "
            "and report generation. Built on LangGraph with DeepSeek V4 "
            "via OpenAI-compatible API."
        ),
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
    )

    # --- 中间件（从内层开始注册） ---

    # 1. 频率限制 — 最内层（请求进入时最先执行）
    app.add_middleware(RateLimitMiddleware)

    # 2. 请求 ID 链路追踪
    app.add_middleware(RequestIDMiddleware)

    # 3. 会话管理
    app.add_middleware(SessionMiddleware)

    # 4. 安全头部
    app.add_middleware(SecurityHeadersMiddleware)

    # 5. CORS — 最外层（优先处理预检请求）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- 路由 ---
    app.include_router(api_router, prefix="/api/v1")

    # --- WebSocket 路由 ---
    app.add_api_websocket_route("/ws/{session_id}", websocket_endpoint)

    # --- 健康检查（无需认证，不受频率限制） ---
    @app.get("/health", include_in_schema=False)
    async def health_check():
        """
        容器编排的存活/就绪探针。

        如果进程存活则返回 200。不检查下游依赖（数据库、Redis）——
        如有需要可通过 /health/ready 单独检查。
        """
        return {
            "status": "healthy",
            "version": settings.app_version,
            "environment": settings.environment,
        }

    # --- 静态文件（前端 SPA） ---
    # 在 API 路由之后挂载，确保 /api/v1/* 和 /ws/* 优先匹配。
    # html=True 在目录请求时返回 index.html（SPA 降级方案）。
    app.mount("/", StaticFiles(directory="app/static", html=True), name="static")

    return app


# --- 模块级单例（供 uvicorn 使用：`uvicorn app.api.main:app`） ---
app = create_app()
