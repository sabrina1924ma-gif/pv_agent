"""
HTTP middleware layer: session management, rate limiting, request tracing.

Middleware stack order (from outermost to innermost, executed in reverse):
    1. SecurityHeadersMiddleware  — HSTS, X-Content-Type-Options, CSP, etc.
    2. CORSMiddleware             — preflight / cross-origin (built-in)
    3. SessionMiddleware          — assign / validate session_id
    4. RequestIDMiddleware        — inject X-Request-Id for tracing
    5. RateLimitMiddleware        — throttle requests per client IP

All custom middleware uses the ASGI pure-ASGI pattern (BaseHTTPMiddleware)
for compatibility with FastAPI's lifespan and background task handling.

SessionMiddleware and RateLimitMiddleware depend only on config — no agent
dependency — so they are testable via curl immediately after deployment.
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
# SecurityHeadersMiddleware — defensive HTTP headers
# ============================================================================

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Inject security-related HTTP response headers.

    Headers set:
        X-Content-Type-Options: nosniff          — prevent MIME sniffing
        X-Frame-Options: DENY                    — prevent clickjacking
        X-XSS-Protection: 1; mode=block          — enable XSS filter
        Strict-Transport-Security: max-age=...    — enforce HTTPS (prod only)
        Cache-Control: no-store (on API routes)   — prevent caching of responses
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)

        # Prevent MIME type sniffing
        response.headers.setdefault("X-Content-Type-Options", "nosniff")

        # Prevent clickjacking
        response.headers.setdefault("X-Frame-Options", "DENY")

        # XSS auditor (legacy but doesn't hurt)
        response.headers.setdefault("X-XSS-Protection", "1; mode=block")

        # API routes should not be cached
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store, no-cache, must-revalidate")

        return response


# ============================================================================
# SessionMiddleware
# ============================================================================

class SessionMiddleware(BaseHTTPMiddleware):
    """
    Assign and validate a session_id for every incoming HTTP request.

    Logic:
        - Read session_id from the X-Session-Id header.
        - If missing or not a valid UUID, generate a new UUIDv4.
        - Inject session_id into request.state for downstream access.
        - Echo the session_id back in the X-Session-Id response header.

    This enables multi-turn conversation tracking across stateless HTTP requests.
    WebSocket sessions use the URL path parameter instead and are handled
    separately in the websocket endpoint.
    """

    HEADER_NAME: str = "X-Session-Id"

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        session_id = request.headers.get(self.HEADER_NAME)

        # Validate existing session_id
        if session_id:
            try:
                uuid.UUID(session_id)
            except (ValueError, AttributeError):
                logger.warning(
                    f"SessionMiddleware: invalid session_id {session_id!r}, "
                    f"generating new one"
                )
                session_id = None

        # Generate new session if needed
        if not session_id:
            session_id = str(uuid.uuid4())
            logger.debug(f"SessionMiddleware: assigned new session_id={session_id}")

        # Inject into request state for downstream handlers
        request.state.session_id = session_id

        # Process the request
        response = await call_next(request)

        # Echo session_id back so clients can persist it
        response.headers[self.HEADER_NAME] = session_id

        return response


# ============================================================================
# RequestIDMiddleware — request tracing
# ============================================================================

class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Assign a unique X-Request-Id to every request for distributed tracing.

    If the client sends an X-Request-Id header, it is validated and reused.
    Otherwise a new UUIDv4 is generated. The ID is injected into request.state
    and echoed in the response headers. Loguru's contextualize() scopes log
    entries with the request ID so every log line is traceable to a request.
    """

    HEADER_NAME: str = "X-Request-Id"

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(self.HEADER_NAME)

        # Validate incoming request ID
        if request_id:
            try:
                uuid.UUID(request_id)
            except (ValueError, AttributeError):
                request_id = None

        if not request_id:
            request_id = str(uuid.uuid4())

        # Store for downstream use
        request.state.request_id = request_id

        # Contextualize all log entries during this request
        with logger.contextualize(request_id=request_id):
            response = await call_next(request)

        # Echo back
        response.headers[self.HEADER_NAME] = request_id

        return response


# ============================================================================
# RateLimitMiddleware — sliding window rate limiter
# ============================================================================

class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Per-IP sliding-window rate limiter (in-memory).

    Tracks request timestamps per client IP. Requests exceeding the
    configured limit within the window receive a 429 response.

    Configuration (from Settings):
        rate_limit_requests:      max requests per window (default 60)
        rate_limit_window_seconds: window duration in seconds (default 60)

    Note:
        This is an in-memory implementation suitable for single-worker
        deployments. For multi-worker setups, replace with a Redis-backed
        sliding window using sorted sets.
    """

    # In-memory store: { client_ip: [timestamp, timestamp, ...] }
    _store: dict[str, list[float]] = defaultdict(list)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        settings = get_settings()
        client_ip = request.client.host if request.client else "unknown"

        now = time.time()
        window_start = now - settings.rate_limit_window_seconds

        # Purge expired timestamps for this client
        self._store[client_ip] = [
            ts for ts in self._store[client_ip] if ts > window_start
        ]

        # Enforce limit
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

        # Record this request
        self._store[client_ip].append(now)

        return await call_next(request)
