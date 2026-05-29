"""
LangGraph 状态机定义。

构建一个已编译的 StateGraph，编排 agent 管线：
    load_history -> intent_router -> {tool_executor?} -> report_node -> END

该图支持条件分支：纯报告意图跳过工具执行，直接进入报告生成。
"""

from __future__ import annotations

import uuid
from typing import NamedTuple

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
# CheckpointTuple — 与 LangGraph 兼容的检查点元组
# ============================================================================

class CheckpointTuple(NamedTuple):
    """与 LangGraph CheckpointTuple 字段匹配的本地定义。"""
    config: dict
    checkpoint: dict
    metadata: dict
    parent_config: dict | None = None
    pending_writes: list | None = None


# ============================================================================
# PostgresCheckpointer — PostgreSQL 后端检查点适配器
# ============================================================================

class PostgresCheckpointer:
    """
    适配器：将 DatabaseManager 暴露为 LangGraph checkpointer 接口。

    实现 LangGraph 期望的 aget_tuple / aput / alist 方法，
    使用我们的 CheckpointRepository 在 PostgreSQL 中持久化状态快照。
    """

    def __init__(self, db_manager) -> None:
        self.db = db_manager

    async def aget_tuple(self, config):
        """获取指定线程的最新检查点。"""
        from app.storage.repositories import CheckpointRepository

        thread_id = config["configurable"]["thread_id"]
        async with self.db.session_context() as session:
            repo = CheckpointRepository(session)
            cp = await repo.get_latest(thread_id)
            if cp is None:
                return None
            return CheckpointTuple(
                config={
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_id": cp.checkpoint_id,
                        "checkpoint_ns": cp.checkpoint_ns,
                    }
                },
                checkpoint=cp.checkpoint,
                metadata=cp.metadata_,
                parent_config=(
                    {
                        "configurable": {
                            "thread_id": thread_id,
                            "checkpoint_id": cp.parent_checkpoint_id,
                        }
                    }
                    if cp.parent_checkpoint_id
                    else None
                ),
            )

    async def aput(self, config, checkpoint, metadata, new_versions):
        """持久化一个检查点快照。"""
        from app.storage.repositories import CheckpointRepository

        thread_id = config["configurable"]["thread_id"]
        checkpoint_id = checkpoint.get("id", str(uuid.uuid4()))
        checkpoint_ns = config.get("configurable", {}).get("checkpoint_ns", "")
        parent_checkpoint_id = config.get("configurable", {}).get("checkpoint_id")
        async with self.db.session_context() as session:
            repo = CheckpointRepository(session)
            await repo.save(
                thread_id=thread_id,
                checkpoint_id=checkpoint_id,
                checkpoint_data=checkpoint,
                checkpoint_ns=checkpoint_ns,
                parent_checkpoint_id=parent_checkpoint_id,
                metadata=metadata,
            )

    async def alist(self, config, *, limit=None, before=None):
        """列出指定线程的检查点。"""
        from app.storage.repositories import CheckpointRepository

        thread_id = config["configurable"]["thread_id"]
        async with self.db.session_context() as session:
            repo = CheckpointRepository(session)
            checkpoints = await repo.list_by_thread(thread_id, limit=limit or 10)
            results = []
            for cp in checkpoints:
                results.append(
                    CheckpointTuple(
                        config={
                            "configurable": {
                                "thread_id": thread_id,
                                "checkpoint_id": cp.checkpoint_id,
                                "checkpoint_ns": cp.checkpoint_ns,
                            }
                        },
                        checkpoint=cp.checkpoint,
                        metadata=cp.metadata_,
                        parent_config=(
                            {
                                "configurable": {
                                    "thread_id": thread_id,
                                    "checkpoint_id": cp.parent_checkpoint_id,
                                }
                            }
                            if cp.parent_checkpoint_id
                            else None
                        ),
                    )
                )
            return results

    async def aput_writes(self, config, writes, task_id):
        """暂不持久化 pending writes，保持实现简洁。"""
        pass

    async def aget_next_version(self, current, key):
        """返回下一个版本号。"""
        return (current or 0) + 1


# ============================================================================
# 图构建器
# ============================================================================

def build_graph() -> CompiledStateGraph:
    """
    构建并编译 LangGraph 状态机。

    节点说明：
        load_history：
            从 Redis 获取近期对话历史，追加到 state 的 messages 列表中，
            提供上下文感知能力。

        intent_router：
            调用 LLM 对用户意图进行分类。设置 state.intent。
            根据意图通过条件边路由到 tool_executor 或 report_node。

        tool_executor：
            根据分类后的意图并发调度一个或多个工具调用。
            在 state.tool_results 中累积结果。

        report_node：
            聚合所有消息和工具结果，发送给 LLM 生成最终 Markdown 报告。
            设置 state.report_md。

    边的路由：
        START -> load_history -> intent_router
        intent_router -> tool_executor  （如需工具）
        intent_router -> report_node    （如无需工具，例如普通聊天）
        tool_executor -> report_node
        report_node -> END

    返回：
        一个已编译的 LangGraph，可通过 graph.ainvoke(initial_state) 调用。
    """
    settings = get_settings()

    # --- 构建图 ---
    workflow: StateGraph = StateGraph(AgentState)

    # 节点
    workflow.add_node("load_history", load_history)
    workflow.add_node("intent_router", intent_router)
    workflow.add_node("tool_executor", tool_executor)
    workflow.add_node("report_node", report_node)

    # --- 定义边 ---

    # 入口：始终先加载历史记录
    workflow.set_entry_point("load_history")

    # load_history -> intent_router（始终）
    workflow.add_edge("load_history", "intent_router")

    # intent_router 有条件分支：
    #   - "needs_tools"   -> tool_executor
    #   - "no_tools"      -> report_node（直接）
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

    # --- 编译 ---
    if settings.langgraph_checkpoint_persist and settings.environment != "development":
        from app.storage.db import get_db_manager

        db = get_db_manager()
        checkpointer = PostgresCheckpointer(db)
        graph = workflow.compile(checkpointer=checkpointer)
        logger.info(
            f"Agent graph compiled with PostgreSQL checkpointer: "
            f"{len(workflow.nodes)} nodes, "
            f"max_recursion={settings.langgraph_max_recursion}"
        )
    else:
        graph = workflow.compile()
        logger.info(
            f"Agent graph compiled (in-memory, no checkpointer): "
            f"{len(workflow.nodes)} nodes, "
            f"max_recursion={settings.langgraph_max_recursion}"
        )

    return graph


# ---------------------------------------------------------------------------
# 懒加载图访问器（首次使用时编译一次，然后缓存）
# 初始化，懒加载
# ---------------------------------------------------------------------------

_app_graph: CompiledStateGraph | None = None


def get_graph() -> CompiledStateGraph:
    """
    返回已编译的 agent 图，首次调用时构建一次。

    延迟编译可防止导入时失败阻塞应用启动，
    并将 LLM/工具依赖推迟到实际需要时才加载。
    """
    global _app_graph
    if _app_graph is None:
        _app_graph = build_graph()
    return _app_graph
