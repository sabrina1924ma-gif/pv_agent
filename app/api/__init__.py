"""
API layer: FastAPI application, middleware, routes, and WebSocket endpoints.

Exports:
    app         — module-level FastAPI singleton (uvicorn entry point)
    create_app  — factory function for custom app creation
"""

from app.api.main import create_app, app

__all__ = ["create_app", "app"]
