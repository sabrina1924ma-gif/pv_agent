"""
WebSocket endpoint for streaming agent responses.

Implements a bidirectional WebSocket channel that streams LLM tokens,
tool-call lifecycle events, and node transitions back to the client in
real time.

Event pipeline:
    astream_graph() (streaming.py) → StreamEvent → WebSocket JSON

The astream_graph() generator yields these event types:
    thinking      — immediate (<10ms), signals processing has started
    node_start    — entering a graph node (load_history, intent_router, ...)
    node_complete — exiting a graph node
    tool_start    — about to invoke a tool function
    tool_complete — tool function returned (success or failure)
    token         — LLM produced a text token (buffered, ~20 chars or newline)
    done          — graph execution complete, final state available
    error         — unrecoverable error during processing

Each is dispatched to the WebSocket as a JSON message with {event, data}.

Behind the scenes, astream_graph() uses LangGraph's ainvoke + LangChain's
astream (ChatOpenAI streaming=True) + the contextvar callback system wired
into every node. The LLM tokens flow through `on_chat_model_stream` at the
LangChain level and are re-emitted as our StreamEvent "token" events.

Protocol (Client → Server):
    {"content": "...", "station_id": "..."}

Protocol (Server → Client):
    {"event": "connected",     "data": {"session_id": "..."}}
    {"event": "thinking",      "data": {"message": "..."}}
    {"event": "node_start",    "data": {"node": "...", "message": "..."}}
    {"event": "node_end",      "data": {"node": "..."}}
    {"event": "tool_start",    "data": {"tool": "...", "message": "..."}}
    {"event": "tool_end",      "data": {"tool": "...", "elapsed_ms": ...}}
    {"event": "token",         "data": {"token": "..."}}
    {"event": "done",          "data": {"report_md": "...", "elapsed_ms": ...}}
    {"event": "error",         "data": {"message": "..."}}
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from langchain_core.messages import HumanMessage

from app.agent.schema import AgentState
from app.agent.streaming import astream_graph
from loguru import logger

# Heartbeat interval: send a ping if no messages for this many seconds.
# Browsers typically timeout idle WebSocket connections after 60s, so 30s
# gives a comfortable safety margin.
HEARTBEAT_SECONDS = 30


# ============================================================================
# Main WebSocket handler
# ============================================================================

async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    """
    WebSocket endpoint for streaming agent interactions.

    Lifecycle per connection:
        1. Accept handshake, send welcome event, start heartbeat task.
        2. Loop: receive a user message, run the graph via astream_graph(),
           dispatch every StreamEvent to the client as JSON.
        3. On disconnect / error, cancel heartbeat, clean up and close.

    Args:
        websocket: FastAPI WebSocket connection.
        session_id: Extracted from URL path /ws/{session_id}.
    """
    # --- 1. Accept connection ---
    await websocket.accept()
    logger.info(f"WebSocket connected: session={session_id}")
    await websocket.send_json({
        "event": "connected",
        "data": {"session_id": session_id},
    })

    # --- 1b. Start heartbeat task ---
    _stop_heartbeat = asyncio.Event()
    _heartbeat_task = asyncio.create_task(
        _heartbeat(websocket, _stop_heartbeat, session_id)
    )

    try:
        # --- 2. Message loop ---
        while True:
            raw = await websocket.receive_text()

            # Parse incoming
            try:
                payload: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({
                    "event": "error",
                    "data": {"message": "Invalid JSON. Expected: {\"content\": \"...\"}"},
                })
                continue

            message_text: str = payload.get("content", "").strip()
            station_id: str | None = payload.get("station_id")

            if not message_text:
                await websocket.send_json({
                    "event": "error",
                    "data": {"message": "Empty content."},
                })
                continue

            logger.info(
                f"WS msg: session={session_id} station={station_id} "
                f"len={len(message_text)}"
            )

            # --- 3. Build initial state ---
            initial_state: AgentState = {
                "messages": [HumanMessage(content=message_text)],
                "session_id": session_id,
                "station_id": station_id,
            }

            # --- 4. Stream graph execution to WebSocket ---
            t0 = time.monotonic()
            emitted_tokens: int = 0

            async for se in astream_graph(initial_state):
                # Convert StreamEvent → WebSocket JSON message
                ws_event = _stream_event_to_ws(se)

                # Track tokens for the final summary
                if se.type == "token" and se.content:
                    emitted_tokens += 1

                # Send to client (this also serves as implicit heartbeat)
                await websocket.send_json(ws_event)

            # --- 5. Final summary log ---
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info(
                f"WS done: session={session_id} tokens={emitted_tokens} "
                f"elapsed={elapsed_ms:.0f}ms"
            )

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: session={session_id}")

    except asyncio.CancelledError:
        logger.info(f"WebSocket task cancelled: session={session_id}")

    except Exception:
        logger.exception(f"WebSocket unhandled error: session={session_id}")
        try:
            await websocket.send_json({
                "event": "error",
                "data": {"message": "Internal server error."},
            })
        except Exception:
            pass
        await websocket.close(code=1011, reason="Internal server error")

    finally:
        # --- Cleanup: stop heartbeat ---
        _stop_heartbeat.set()
        _heartbeat_task.cancel()
        try:
            await _heartbeat_task
        except asyncio.CancelledError:
            pass


# ============================================================================
# Heartbeat — prevents browser/proxy from closing idle connections
# ============================================================================

async def _heartbeat(
    websocket: WebSocket,
    stop: asyncio.Event,
    session_id: str,
) -> None:
    """
    Send a periodic ping to keep the WebSocket alive.

    Runs until `stop` is set or the websocket is disconnected.
    """
    try:
        while not stop.is_set():
            await asyncio.sleep(HEARTBEAT_SECONDS)
            if not stop.is_set():
                await websocket.send_json({"event": "ping"})
                logger.debug(f"WS heartbeat: session={session_id}")
    except Exception:
        # Connection closed — expected, don't log as error
        pass


# ============================================================================
# StreamEvent → WebSocket JSON mapping
# ============================================================================

def _stream_event_to_ws(se: Any) -> dict[str, Any]:
    """
    Convert a StreamEvent from astream_graph() to a WebSocket JSON message.

    Event type mapping:
        thinking      → {"event": "thinking", ...}
        node_start    → {"event": "node_start", ...}
        node_complete → {"event": "node_end", ...}
        tool_start    → {"event": "tool_start", ...}
        tool_complete → {"event": "tool_end", ...}
        token         → {"event": "token", ...}
        done          → {"event": "done", ...}
        error         → {"event": "error", ...}
    """
    etype = se.type

    if etype == "thinking":
        return {
            "event": "thinking",
            "data": {
                "message": se.message or "正在处理您的请求...",
                "elapsed_ms": se.elapsed_ms,
            },
        }

    elif etype == "node_start":
        return {
            "event": "node_start",
            "data": {
                "node": se.node or "",
                "message": se.message or "",
            },
        }

    elif etype == "node_complete":
        return {
            "event": "node_end",
            "data": {
                "node": se.node or "",
                "intent": se.intent or "",
                "elapsed_ms": se.elapsed_ms,
            },
        }

    elif etype == "tool_start":
        return {
            "event": "tool_start",
            "data": {
                "tool": se.tool or "",
                "message": se.message or "",
            },
        }

    elif etype == "tool_complete":
        return {
            "event": "tool_end",
            "data": {
                "tool": se.tool or "",
                "message": se.message or "",
                "elapsed_ms": se.elapsed_ms,
            },
        }

    elif etype == "token":
        return {
            "event": "token",
            "data": {"token": se.content or ""},
        }

    elif etype == "done":
        return {
            "event": "done",
            "data": {
                "message": se.message or "处理完成",
                "intent": se.intent or "",
                "report_md": se.content or "",
                "elapsed_ms": se.elapsed_ms,
            },
        }

    elif etype == "error":
        return {
            "event": "error",
            "data": {
                "message": se.message or se.content or "Unknown error",
            },
        }

    # Fallback: unknown event types
    return {
        "event": "unknown",
        "data": {"type": etype, "content": se.content or ""},
    }
