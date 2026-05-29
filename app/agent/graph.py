"""
LangGraph state machine definition.

Builds a compiled StateGraph that orchestrates the agent pipeline:
    load_history -> intent_router -> {tool_executor?} -> report_node -> END

The graph supports conditional branching: report-only intents skip tool
execution and go straight to report generation.
"""

from __future__ import annotations

from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph

from app.agent.schema import AgentState
from app.agent.nodes.load_history import load_history
from app.agent.nodes.intent_router import intent_router, route_after_intent
from app.agent.nodes.tool_executor import tool_executor
from app.agent.nodes.report_node import report_node
from app.config import get_settings
from loguru import logger


# ============================================================================
# Graph Builder
# ============================================================================

def build_graph() -> CompiledStateGraph:
    """
    Construct and compile the LangGraph state machine.

    Node descriptions:
        load_history:
            Fetches recent conversation history from Redis and appends it
            to the state's messages list for contextual awareness.

        intent_router:
            Calls the LLM to classify the user's intent. Sets state.intent.
            Conditional edge routes to tool_executor or report_node based on intent.

        tool_executor:
            Dispatches one or more tool calls in parallel based on the
            classified intent. Accumulates results in state.tool_results.

        report_node:
            Aggregates all messages and tool_results, sends them to the LLM
            for final Markdown report generation. Sets state.report_md.

    Edge routing:
        START -> load_history -> intent_router
        intent_router -> tool_executor  (if tools are needed)
        intent_router -> report_node    (if no tools are needed, e.g. general chat)
        tool_executor -> report_node
        report_node -> END

    Returns:
        A compiled LangGraph that can be invoked via: graph.ainvoke(initial_state)
    """
    settings = get_settings()

    # --- Build the graph ---
    workflow: StateGraph = StateGraph(AgentState)

    # nodes
    workflow.add_node("load_history", load_history)
    workflow.add_node("intent_router", intent_router)
    workflow.add_node("tool_executor", tool_executor)
    workflow.add_node("report_node", report_node)

    # --- Define edges ---

    # Entry: always load history first
    workflow.set_entry_point("load_history")

    # load_history -> intent_router (always)
    workflow.add_edge("load_history", "intent_router")

    # intent_router has a conditional branch:
    #   - "needs_tools"   -> tool_executor
    #   - "no_tools"      -> report_node (direct)
    workflow.add_conditional_edges(
        "intent_router",
        route_after_intent,
        {
            "needs_tools": "tool_executor",
            "no_tools": "report_node",
        },
    )

    # tool_executor -> report_node
    workflow.add_edge("tool_executor", "report_node")

    # report_node -> END
    workflow.add_edge("report_node", END)

    # --- Compile ---
    # todo In production, attach a PostgreSQL checkpointer for state persistence:
    #   from app.storage.db import async_checkpointer
    #   graph = workflow.compile(checkpointer=async_checkpointer)
    graph = workflow.compile()

    logger.info(
        f"Agent graph compiled: {len(workflow.nodes)} nodes, "
        f"max_recursion={settings.langgraph_max_recursion}"
    )

    return graph


# ---------------------------------------------------------------------------
# Lazy graph accessor (compiled once on first use, then cached)
# 初始化，懒加载
# ---------------------------------------------------------------------------

_app_graph: CompiledStateGraph | None = None


def get_graph() -> CompiledStateGraph:
    """
    Return the compiled agent graph, building it once on first call.

    Lazy compilation prevents import-time failures from blocking app startup,
    and defers LLM/tool dependencies until they are actually needed.
    """
    global _app_graph
    if _app_graph is None:
        _app_graph = build_graph()
    return _app_graph
