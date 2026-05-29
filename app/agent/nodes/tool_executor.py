"""
Node: tool_executor

根据意图并发调度工具，聚合执行结果。

数据流:
    入: state["intent"], state["station_id"], state["query_params"]
    出: state["tool_results"], state["messages"] (追加 ToolMessage)

工具调用采用 asyncio.gather 并发执行，每个工具独立超时和异常隔离——
单个工具失败不影响其他工具的执行。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from langchain_core.messages import ToolMessage

from app.agent.schema import AgentState
from app.agent.events import emit, StreamEvent
from app.config import get_settings
from loguru import logger

# 导入触发工具注册
import app.tools.impl  # noqa: F401 — side-effect import for TOOL_REGISTRY
from app.tools.decorator import TOOL_REGISTRY


# ============================================================================
# Intent → Tool 映射
# ============================================================================

INTENT_TOOL_MAP: dict[str, list[str]] = {
    "device_status":         ["device_status"],
    "power_curve":           ["power_curve"],
    "fault_detection":       ["fault_detection"],
    "life_assessment":       ["life_assessment"],
    "report":                ["report_generator"],
    "multi_station_summary": ["multi_station_summary"],
}


# ============================================================================
# 为每种 intent 构建工具调用参数
# ============================================================================

def _build_tool_kwargs(
    tool_name: str,
    intent: str,
    station_id: str | None,
    query_params: dict[str, Any],
) -> dict[str, Any]:
    """根据工具名和 state 构建调用参数。

    不同工具接受不同参数——这里按白名单方式只传该工具需要的，
    避免 TypeError: unexpected keyword argument。
    """
    kwargs: dict[str, Any] = {}

    # 大多数工具需要 station_id
    if station_id and tool_name in (
        "device_status", "power_curve", "fault_detection",
        "life_assessment", "report_generator",
    ):
        kwargs["station_id"] = station_id

    # power_curve: 额外接受 year, month, resolution
    if tool_name == "power_curve":
        if "year" in query_params:
            kwargs["year"] = query_params["year"]
        if "month" in query_params:
            kwargs["month"] = query_params["month"]
        kwargs.setdefault("resolution", query_params.get("resolution", "daily"))

    # fault_detection: 接受 start_date, end_date
    if tool_name == "fault_detection":
        if "start_date" in query_params:
            kwargs["start_date"] = query_params["start_date"]
        if "end_date" in query_params:
            kwargs["end_date"] = query_params["end_date"]

    # report_generator: 接受 year, report_type
    if tool_name == "report_generator":
        if "year" in query_params:
            kwargs["year"] = query_params["year"]
        kwargs.setdefault("report_type", query_params.get("report_type", "annual"))

    # multi_station_summary: 接受 station_ids (list), metric, year
    if tool_name == "multi_station_summary":
        # 如果没有指定电站列表，默认对比全部 5 个电站
        kwargs["station_ids"] = query_params.get(
            "station_ids",
            ["station-001", "station-002", "station-003", "station-004", "station-005"],
        )
        kwargs["metric"] = query_params.get("metric", "total_generation")
        if "year" in query_params:
            kwargs["year"] = query_params["year"]

    return kwargs


# ============================================================================
# 单工具执行（含超时、异常隔离）
# ============================================================================

async def _run_one_tool(
    tool_name: str,
    kwargs: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    """执行单个工具，返回统一的结果字典。"""
    start = time.monotonic()

    await emit(StreamEvent(
        type="tool_start", tool=tool_name,
        message=f"正在调用工具: {tool_name}...",
    ))

    tool_fn = TOOL_REGISTRY.get(tool_name)
    if tool_fn is None:
        return {
            "tool_name": tool_name,
            "status": "error",
            "error": f"Tool '{tool_name}' not found in registry",
            "data": None,
            "elapsed_ms": 0,
        }

    try:
        result = await asyncio.wait_for(
            tool_fn(**kwargs),
            timeout=timeout_seconds,
        )
        elapsed = (time.monotonic() - start) * 1000
        logger.info(f"tool_executor: {tool_name} OK ({elapsed:.0f}ms)")
        await emit(StreamEvent(
            type="tool_complete", tool=tool_name,
            message=f"✓ {tool_name} 完成 ({elapsed:.0f}ms)",
            elapsed_ms=round(elapsed, 1),
        ))
        return {
            "tool_name": tool_name,
            "status": "ok",
            "data": result,
            "elapsed_ms": round(elapsed, 1),
        }
    except asyncio.TimeoutError:
        elapsed = (time.monotonic() - start) * 1000
        logger.error(f"tool_executor: {tool_name} timed out after {timeout_seconds}s")
        await emit(StreamEvent(
            type="tool_complete", tool=tool_name,
            message=f"✗ {tool_name} 超时",
            elapsed_ms=round(elapsed, 1),
        ))
        return {
            "tool_name": tool_name,
            "status": "error",
            "error": f"Timed out after {timeout_seconds}s",
            "data": None,
            "elapsed_ms": round(elapsed, 1),
        }
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        logger.exception(f"tool_executor: {tool_name} failed: {e}")
        await emit(StreamEvent(
            type="tool_complete", tool=tool_name,
            message=f"✗ {tool_name} 失败: {e}",
            elapsed_ms=round(elapsed, 1),
        ))
        return {
            "tool_name": tool_name,
            "status": "error",
            "error": str(e),
            "data": None,
            "elapsed_ms": round(elapsed, 1),
        }


# ============================================================================
# 主节点
# ============================================================================

async def tool_executor(state: AgentState) -> AgentState:
    """根据意图并发执行工具，聚合结果。

    容错策略:
        - 单个工具失败不影响其他工具（异常隔离）
        - 每个工具有独立超时（asyncio.wait_for）
        - 工具未注册/未找到时返回 error 状态，不抛异常
    """
    settings = get_settings()
    intent = state.get("intent", "general_chat")
    station_id = state.get("station_id")
    query_params = state.get("query_params", {})

    tool_names = INTENT_TOOL_MAP.get(intent, [])

    if not tool_names:
        logger.debug(f"tool_executor: no tools for intent={intent}, skipping")
        state["tool_results"] = []
        return state

    logger.info(
        f"tool_executor: intent={intent}, "
        f"tools={tool_names}, station={station_id}, params={query_params}"
    )

    # 并发执行所有工具
    tasks = []
    for name in tool_names:
        kwargs = _build_tool_kwargs(name, intent, station_id, query_params)
        tasks.append(_run_one_tool(name, kwargs, settings.llm_timeout_seconds))

    results = await asyncio.gather(*tasks)
    state["tool_results"] = list(results)

    # 将工具结果追加为 ToolMessage（供 report_node 的 LLM 消费）
    # LangGraph 的 messages 字段使用 add_messages reducer，所以可以只传递新增消息，
    # reducer 会自动合并到已有 messages 中。这样避免了 in-place mutation。
    tool_call_id = f"call_{intent}_{int(time.time())}"
    tool_messages: list[ToolMessage] = []
    for r in results:
        content = json.dumps(r, ensure_ascii=False, default=str)
        tool_messages.append(ToolMessage(
            content=content,
            tool_call_id=tool_call_id,
            name=r["tool_name"],
        ))
    state["messages"] = tool_messages  # add_messages reducer 自动合并

    ok_count = sum(1 for r in results if r["status"] == "ok")
    logger.info(
        f"tool_executor: {ok_count}/{len(results)} tools succeeded"
    )

    return state
