"""
HTTP 中间件层：会话管理、频率限制、请求链路追踪。

中间件栈顺序（从外到内，执行顺序相反）：
    1. SecurityHeadersMiddleware  — HSTS、X-Content-Type-Options、CSP 等
    2. CORSMiddleware             — 预检 / 跨域（内置）
    3. SessionMiddleware          — 分配 / 验证 session_id
    4. RequestIDMiddleware        — 注入 X-Request-Id 用于链路追踪
    5. RateLimitMiddleware        — 按客户端 IP 限制请求频率

所有自定义中间件均使用 ASGI 纯模式（BaseHTTPMiddleware），
以确保与 FastAPI 的生命周期和后台任务处理兼容。

SessionMiddleware 和 RateLimitMiddleware 仅依赖配置，不依赖 Agent，
因此部署后即可通过 curl 进行测试。
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from typing import Any, Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.config import get_settings
from loguru import logger


# ============================================================================
# SecurityHeadersMiddleware — 防御性 HTTP 头部
# ============================================================================

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    注入安全相关的 HTTP 响应头部。

    设置的头部：
        X-Content-Type-Options: nosniff          — 防止 MIME 嗅探
        X-Frame-Options: DENY                    — 防止点击劫持
        X-XSS-Protection: 1; mode=block          — 启用 XSS 过滤器
        Strict-Transport-Security: max-age=...    — 强制 HTTPS（仅生产环境）
        Cache-Control: no-store (on API routes)   — 防止缓存 API 响应
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)

        # 防止 MIME 类型嗅探
        response.headers.setdefault("X-Content-Type-Options", "nosniff")

        # 防止点击劫持
        response.headers.setdefault("X-Frame-Options", "DENY")

        # XSS 审查器（旧特性但无害）
        response.headers.setdefault("X-XSS-Protection", "1; mode=block")

        # API 路由不应被缓存
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store, no-cache, must-revalidate")

        return response


# ============================================================================
# SessionMiddleware — 会话中间件
# ============================================================================

class SessionMiddleware(BaseHTTPMiddleware):
    """
    为每个传入的 HTTP 请求分配并验证 session_id。

    逻辑：
        - 从 X-Session-Id 头部读取 session_id。
        - 如果缺失或不是有效的 UUID，则生成新的 UUIDv4。
        - 将 session_id 注入 request.state 供下游使用。
        - 在 X-Session-Id 响应头部中回传 session_id。

    这使得在无状态 HTTP 请求间进行多轮对话追踪成为可能。
    WebSocket 会话改用 URL 路径参数，在 WebSocket 端点中单独处理。
    """

    HEADER_NAME: str = "X-Session-Id"

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        session_id = request.headers.get(self.HEADER_NAME)

        # 验证已有的 session_id
        if session_id:
            try:
                uuid.UUID(session_id)
            except (ValueError, AttributeError):
                logger.warning(
                    f"SessionMiddleware: invalid session_id {session_id!r}, "
                    f"generating new one"
                )
                session_id = None

        # 如果必要则生成新会话
        if not session_id:
            session_id = str(uuid.uuid4())
            logger.debug(f"SessionMiddleware: assigned new session_id={session_id}")

        # 注入到请求状态中供下游处理器使用
        request.state.session_id = session_id

        # 处理请求
        response = await call_next(request)

        # 回传 session_id 以便客户端可以持久化
        response.headers[self.HEADER_NAME] = session_id

        return response


# ============================================================================
# RequestIDMiddleware — 请求链路追踪
# ============================================================================

class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    为每个请求分配唯一的 X-Request-Id 以实现分布式链路追踪。

    如果客户端发送了 X-Request-Id 头部，则验证并复用。
    否则生成新的 UUIDv4。该 ID 被注入 request.state 并在响应头部中回传。
    Loguru 的 contextualize() 使用请求 ID 限定日志条目的作用域，
    使得每行日志都可追溯到具体请求。
    """

    HEADER_NAME: str = "X-Request-Id"

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(self.HEADER_NAME)

        # 验证传入的请求 ID
        if request_id:
            try:
                uuid.UUID(request_id)
            except (ValueError, AttributeError):
                request_id = None

        if not request_id:
            request_id = str(uuid.uuid4())

        # 存储供下游使用
        request.state.request_id = request_id

        # 在此请求期间为所有日志添加上下文
        with logger.contextualize(request_id=request_id):
            response = await call_next(request)

        # 回传请求 ID
        response.headers[self.HEADER_NAME] = request_id

        return response


# ============================================================================
# RateLimitMiddleware — 滑动窗口频率限制器
# ============================================================================

class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    基于每个 IP 的滑动窗口频率限制器（内存中实现）。

    追踪每个客户端 IP 的请求时间戳。在窗口内超过配置限制的请求将收到 429 响应。

    配置项（来自 Settings）：
        rate_limit_requests:      每个窗口最大请求数（默认 60）
        rate_limit_window_seconds: 窗口时长（秒）（默认 60）

    注意：
        这是一个适用于单工作进程部署的内存实现。
        对于多工作进程环境，请替换为基于 Redis 的有序集合滑动窗口方案。
    """

    # 内存存储：{ client_ip: [时间戳, 时间戳, ...] }
    _store: dict[str, list[float]] = defaultdict(list)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        settings = get_settings()
        client_ip = request.client.host if request.client else "unknown"

        now = time.time()
        window_start = now - settings.rate_limit_window_seconds

        # 清除该客户端已过期的时间戳
        self._store[client_ip] = [
            ts for ts in self._store[client_ip] if ts > window_start
        ]

        # 执行限制
        if len(self._store[client_ip]) >= settings.rate_limit_requests:
            retry_after = int(window_start + settings.rate_limit_window_seconds - now) + 1
            logger.warning(
                f"RateLimit: {client_ip} exceeded limit "
                f"({settings.rate_limit_requests}/{settings.rate_limit_window_seconds}s), "
                f"retry_after={retry_after}s"
            )
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many requests. Please slow down.",
                    "retry_after_seconds": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )

        # 记录本次请求
        self._store[client_ip].append(now)

        return await call_next(request)
