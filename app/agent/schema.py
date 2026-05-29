"""
Agent state definitions using Pydantic.

Defines the shape of the state object that flows through every node
in the LangGraph state machine. Uses TypedDict-style annotation for
LangGraph compatibility, but with Pydantic validation at the boundaries.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


# ============================================================================
# AgentState — the central state object flowing through the graph
# ============================================================================

class AgentState(TypedDict, total=False):
    """
    Shared state that passes through every node in the LangGraph.

    Only 'messages' and 'session_id' are required at invoke time.
    Intermediate fields (intent, tool_results, etc.) are populated by nodes.

    Fields:
        messages:           Full conversation history. Annotated with add_messages.
        session_id:         Unique session identifier (required at invoke).
        intent:             Classified intent label → set by intent_router.
        tool_results:       Tool invocation results → set by tool_executor.
        station_id:         Target station ID → set by intent_router (regex).
        query_params:       Extracted query params → set by intent_router.
        report_md:          Generated Markdown report → set by report_node.
        error:              Error message if unrecoverable → set by any node.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    intent: str | None
    tool_results: list[dict[str, Any]]
    session_id: str
    station_id: str | None
    query_params: dict[str, Any]
    report_md: str | None
    error: str | None
