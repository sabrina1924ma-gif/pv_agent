"""
使用 Pydantic 的 Agent 状态定义。

定义在 LangGraph 状态机中流经每个节点的状态对象结构。
使用 TypedDict 风格注解以兼容 LangGraph，但在边界处使用 Pydantic 验证。
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


# ============================================================================
# AgentState — 流经图的中心状态对象
# ============================================================================

class AgentState(TypedDict, total=False):
    """
    流经 LangGraph 每个节点的共享状态。

    调用时只需要 'messages'、'session_id' 和 'user_id'。
    中间字段（intent、tool_results 等）由各节点填充。

    字段：
        messages：          完整对话历史。使用 add_messages 注解。
        session_id：        唯一会话标识（调用时必需）。
        user_id：           用户 ID（用户隔离，调用时必需）。
        intent：            分类后的意图标签 → 由 intent_router 设置。
        tool_results：      工具调用结果 → 由 tool_executor 设置。
        station_id：        目标电站 ID → 由 intent_router 设置（正则提取）。
        query_params：      提取的查询参数 → 由 intent_router 设置。
        report_md：         生成的 Markdown 报告 → 由 report_node 设置。
        error：             不可恢复时的错误信息 → 由任意节点设置。
        retrieved_docs：    RAG 检索到的知识库文档片段 → 由 retrieve_context 设置。
        retrieved_memories： RAG 检索到的历史会话摘要 → 由 retrieve_context 设置。
    """

    messages: Annotated[list[BaseMessage], add_messages]
    intent: str | None
    tool_results: list[dict[str, Any]]
    session_id: str
    user_id: str
    station_id: str | None
    query_params: dict[str, Any]
    report_md: str | None
    error: str | None
    retrieved_docs: list[dict[str, Any]]
    retrieved_memories: list[dict[str, Any]]
