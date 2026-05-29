"""
FastAPI application entry point.

Orchestrates application creation: configures middleware, mounts routers,
initializes the agent graph and connection pools on startup, and gracefully
shuts them down on exit.

Middleware execution order (request → innermost to outermost):
    1. RateLimitMiddleware      — innermost: throttle requests per IP
    2. RequestIDMiddleware       — inject X-Request-Id for tracing
    3. SessionMiddleware         — assign / validate session_id
    4. SecurityHeadersMiddleware — defensive HTTP headers
    5. CORSMiddleware            — outermost: handle preflight / cross-origin
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
# Application Lifespan (startup / shutdown hooks)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """
    Manage application lifecycle.

    Startup:
        - Compile and cache the LangGraph state graph.
        - Warm up Redis and PostgreSQL connection pools.

    Shutdown:
        - Gracefully close connection pools.
        - Release any remaining resources.
    """
    settings = get_settings()
    logger.info(
        f"Starting {settings.app_name} v{settings.app_version} "
        f"({settings.environment} mode)"
    )

    # --- Startup: compile agent graph ---
    logger.info("Compiling agent graph...")
    application.state.graph = get_graph()
    logger.info("Agent graph ready")

    # --- Startup: pre-warm DB pools ---
    try:
        from app.storage.db import get_db_manager
        db = get_db_manager()
        await db.connect()
        application.state.db_engine = db.engine
        logger.info("Database engine initialized")
    except Exception:
        logger.warning("Database not available — running without persistence")
        application.state.db_engine = None

    # --- Startup: verify Redis connectivity ---
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

    # --- Shutdown ---
    logger.info("Shutting down...")

    # Dispose DB engine
    engine = getattr(application.state, "db_engine", None)
    if engine is not None:
        await engine.dispose()
        logger.info("Database engine disposed")

    # Disconnect Redis
    redis = getattr(application.state, "redis_client", None)
    if redis is not None:
        try:
            await redis.disconnect()
            logger.info("Redis connection closed")
        except Exception:
            pass

    logger.info("Shutdown complete")


# ---------------------------------------------------------------------------
# Application Factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """
    Build and configure the FastAPI application.

    Middleware is added inner-first (last-registered is outermost).
    See the module docstring for the execution order.

    Returns:
        Fully configured FastAPI app instance.
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

    # --- Middleware (register innermost first) ---

    # 1. Rate limiting — innermost (executed first on the way in)
    app.add_middleware(RateLimitMiddleware)

    # 2. Request ID tracing
    app.add_middleware(RequestIDMiddleware)

    # 3. Session management
    app.add_middleware(SessionMiddleware)

    # 4. Security headers
    app.add_middleware(SecurityHeadersMiddleware)

    # 5. CORS — outermost (handles preflight before anything else)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Routes ---
    app.include_router(api_router, prefix="/api/v1")

    # --- WebSocket ---
    app.add_api_websocket_route("/ws/{session_id}", websocket_endpoint)

    # --- Health check (no auth, no rate limit bypass) ---
    @app.get("/health", include_in_schema=False)
    async def health_check():
        """
        Liveness / readiness probe for container orchestration.

        Returns 200 if the process is alive. Does not check downstream
        dependencies (DB, Redis) — those are checked separately via
        /health/ready if needed.
        """
        return {
            "status": "healthy",
            "version": settings.app_version,
            "environment": settings.environment,
        }

    # --- Static files (frontend SPA) ---
    # Mounted after API routes so /api/v1/* and /ws/* take precedence.
    # html=True serves index.html for directory requests (SPA fallback).
    app.mount("/", StaticFiles(directory="app/static", html=True), name="static")

    return app


# --- Module-level singleton (used by uvicorn: `uvicorn app.api.main:app`) ---
app = create_app()
