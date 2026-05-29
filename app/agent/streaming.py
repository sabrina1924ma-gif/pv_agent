"""
流式响应系统。

提供异步生成器 astream_graph()，在 graph 执行过程中持续产出 StreamEvent，
供上层（FastAPI SSE / WebSocket）逐条推送给前端。

使用方式:
    async for event in astream_graph(initial_state):
        yield f"data: {json.dumps(event)}\\n\\n"
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from app.agent.events import (
    StreamEvent,
    set_stream_callback,
)
from app.agent.schema import AgentState
from loguru import logger


# ============================================================================
# 流式图执行入口
# ============================================================================

async def astream_graph(
    initial_state: AgentState,
) -> AsyncIterator[StreamEvent]:
    """流式执行整个 LangGraph，产出 StreamEvent 序列。

    保证:
        - 首个事件在 <10ms 内产出（thinking 事件）
        - report_node 中的 LLM 输出以 token 事件逐片产出
        - 中间节点产出 node_start/node_complete/tool_start/tool_complete 事件
    """
    start_time = asyncio.get_event_loop().time()
    event_queue: asyncio.Queue[StreamEvent | None] = asyncio.Queue()

    async def _on_event(event: StreamEvent) -> None:
        # 调试：记录 token 事件到达队列的时机
        if event.type == "token":
            logger.debug(
                f"streaming: token event → queue "
                f"(len={len(event.content)}, total_events_queued≈{event_queue.qsize()})"
            )
        await event_queue.put(event)

    set_stream_callback(_on_event)

    # --- 立即产出 thinking 事件（<10ms）---
    t0 = asyncio.get_event_loop().time()
    yield StreamEvent(
        type="thinking",
        message="正在处理您的请求...",
        elapsed_ms=0,
    )

    # --- 后台运行 graph（延迟导入避免循环依赖）---
    _graph_result: dict | None = None

    async def _run_graph() -> None:
        nonlocal _graph_result
        try:
            from app.agent.graph import build_graph  # noqa: PLC0415 — 延迟导入
            graph = build_graph()
            _graph_result = await graph.ainvoke(initial_state)
            if isinstance(_graph_result, dict) and _graph_result.get("error"):
                await event_queue.put(StreamEvent(
                    type="error",
                    message=_graph_result["error"],
                    content=_graph_result["error"],
                ))
        except Exception as e:
            logger.exception(f"streaming: graph execution failed: {e}")
            await event_queue.put(StreamEvent(
                type="error",
                message=str(e),
                content=str(e),
            ))
        finally:
            await event_queue.put(None)
            set_stream_callback(None)

    task = asyncio.create_task(_run_graph())

    # --- 消费事件队列 ---
    while True:
        event = await event_queue.get()
        if event is None:
            break
        elapsed = (asyncio.get_event_loop().time() - t0) * 1000
        event.elapsed_ms = round(elapsed, 1)
        yield event

    await task

    total_elapsed = (asyncio.get_event_loop().time() - start_time) * 1000

    # 将最终图结果包含在 done 事件中
    result = _graph_result or {}
    done_event = StreamEvent(
        type="done",
        message="处理完成",
        elapsed_ms=round(total_elapsed, 1),
    )
    # 附加最终状态字段供下游消费者使用（例如 WebSocket）
    if isinstance(result, dict):
        done_event.intent = result.get("intent", "")
        done_event.content = result.get("report_md", "")  # reuse 'content' for report
    yield done_event
