"""
REST API route definitions.

All routes are prefixed with /api/v1. Requests flow through the agent graph,
which orchestrates intent classification, tool calls, and report generation.

HTTP context: graph.ainvoke() runs in FastAPI's event loop. Any async
operations inside LangGraph nodes (LLM calls, tool I/O) correctly
participate in the same loop thanks to asyncio cooperative scheduling.

Endpoints:
    POST   /api/v1/chat               — non-streaming chat, returns full response
    GET    /api/v1/sessions/{id}      — retrieve session metadata
    DELETE /api/v1/sessions/{id}      — clear session context
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from app.agent.graph import get_graph
from app.agent.schema import AgentState
from loguru import logger

api_router = APIRouter(tags=["Agent"])


# ============================================================================
# Request / Response Schemas
# ============================================================================

class ChatRequest(BaseModel):
    """Incoming chat message from the user."""

    message: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description="User's natural-language query about power/equipment data",
    )
    station_id: str | None = Field(
        default=None,
        description="Optional station/device identifier to scope the query",
    )


class ChatResponse(BaseModel):
    """Response from the agent after processing the user's query."""

    session_id: str = Field(..., description="Session identifier for follow-up queries")
    message: str = Field(..., description="Agent's text response / summary")
    intent: str | None = Field(
        default=None, description="Classified intent of the user's query"
    )
    report_md: str | None = Field(
        default=None,
        description="Generated Markdown report (populated when intent triggers report generation)",
    )
    tool_results: list[dict[str, Any]] | None = Field(
        default=None,
        description="Raw results from invoked tools, for debugging and display",
    )
    error: str | None = Field(
        default=None,
        description="Error message if the agent encountered an unrecoverable error",
    )


class SessionInfo(BaseModel):
    """Metadata about a conversation session."""

    session_id: str = Field(..., description="Session identifier")
    message_count: int = Field(
        default=0, description="Number of messages in this session"
    )
    created_at: str | None = Field(
        default=None, description="Session creation timestamp (ISO 8601)"
    )


# ============================================================================
# Routes
# ============================================================================

@api_router.post("/chat", response_model=ChatResponse, status_code=200)
async def chat(request: Request, body: ChatRequest) -> ChatResponse:
    """
    Non-streaming chat endpoint.

    Processes the user's message through the full LangGraph agent pipeline
    and returns a complete response synchronously.

    Pipeline: load_history → intent_router → (tool_executor?) → report_node → respond

    The graph is compiled once and cached (see get_graph()), so repeated calls
    reuse the same graph instance. State is not persisted between calls unless
    a LangGraph checkpointer is configured.
    """
    session_id: str = getattr(request.state, "session_id", "unknown")
    request_id: str = getattr(request.state, "request_id", "unknown")

    logger.info(
        f"POST /chat session={session_id} station={body.station_id} "
        f"msg_len={len(body.message)}"
    )

    # ------------------------------------------------------------------
    # 1. Build initial graph state
    # ------------------------------------------------------------------
    initial_state: AgentState = {
        "messages": [HumanMessage(content=body.message)],
        "session_id": session_id,
        "station_id": body.station_id,
    }

    # ------------------------------------------------------------------
    # 2. Invoke the agent graph
    # ------------------------------------------------------------------
    try:
        graph = get_graph()
        result = await graph.ainvoke(initial_state)
    except Exception as exc:
        logger.exception(f"Graph invocation failed for session={session_id}")
        raise HTTPException(
            status_code=500,
            detail="Agent processing failed. Please try again.",
        ) from exc

    # ------------------------------------------------------------------
    # 3. Extract results from graph state
    # ------------------------------------------------------------------
    intent: str | None = result.get("intent")
    report_md: str | None = result.get("report_md")
    tool_results: list[dict[str, Any]] | None = result.get("tool_results")
    error: str | None = result.get("error")

    # Build a user-friendly summary message
    if error:
        summary = f"处理请求时出现错误: {error}"
    elif report_md:
        # Truncate the report for the summary field — the full report is in report_md
        first_line = report_md.split("\n", 1)[0] if report_md else ""
        summary = first_line[:200] if first_line else "报告已生成"
    else:
        summary = f"已处理您的请求 (意图: {intent or '未分类'})"

    return ChatResponse(
        session_id=session_id,
        message=summary,
        intent=intent,
        report_md=report_md,
        tool_results=tool_results,
        error=error,
    )


@api_router.get("/sessions/{session_id}", response_model=SessionInfo)
async def get_session(session_id: str, request: Request) -> SessionInfo:
    """
    Retrieve metadata for a given conversation session.

    TODO (future): Query Redis for hot session data and PostgreSQL for
    warm/cold archived sessions. Currently returns a stub.
    """
    logger.info(f"GET /sessions/{session_id}")

    # Validate UUID format
    from uuid import UUID

    try:
        UUID(session_id)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid session_id format: {session_id!r}",
        )

    # Stub — in production, query Redis + PostgreSQL
    return SessionInfo(
        session_id=session_id,
        message_count=0,
        created_at=None,
    )


@api_router.delete("/sessions/{session_id}", status_code=200)
async def clear_session(session_id: str, request: Request) -> dict[str, str]:
    """
    Clear a session's conversation context.

    Removes session data from Redis (hot cache) and marks the session
    as archived in PostgreSQL. Returns an acknowledgment.

    TODO (future): Wire up Redis deletion + PostgreSQL archival.
    """
    from uuid import UUID

    try:
        UUID(session_id)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid session_id format: {session_id!r}",
        )

    logger.info(f"DELETE /sessions/{session_id}")

    # TODO: Delete from Redis, archive in PostgreSQL
    #   await redis_client.delete(f"session:{session_id}")
    #   await db.execute(update(Session).where(...).values(archived=True))

    return {"status": "ok", "session_id": session_id}
