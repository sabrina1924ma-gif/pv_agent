"""
用于流式传输 Agent 响应的 WebSocket 端点。

实现一个双向 WebSocket 通道，实时将 LLM 令牌、
工具调用生命周期事件和节点转换推送给客户端。

事件管线：
    astream_graph() (streaming.py) → StreamEvent → WebSocket JSON

astream_graph() 生成器产生以下事件类型：
    thinking      — 即时（<10ms），表示处理已开始
    node_start    — 进入图节点（load_history、intent_router...）
    node_complete — 退出图节点
    tool_start    — 即将调用工具函数
    tool_complete — 工具函数返回（成功或失败）
    token         — LLM 生成文本令牌（缓冲，约 20 字符或换行）
    done          — 图执行完成，最终状态可用
    error         — 处理过程中出现不可恢复的错误

每个事件以 {event, data} 格式的 JSON 消息分发给 WebSocket。

在幕后，astream_graph() 使用 LangGraph 的 ainvoke + LangChain 的
astream (ChatOpenAI streaming=True) + 注入到每个节点的
contextvar 回调系统。LLM 令牌通过 LangChain 层的 `on_chat_model_stream`
流动，并作为我们的 StreamEvent "token" 事件重新发出。

协议（客户端 → 服务器）：
    {"content": "...", "station_id": "..."}

协议（服务器 → 客户端）：
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

# 心跳间隔：如果在此秒数内没有消息则发送 ping。
# 浏览器通常在 60 秒后超时空闲 WebSocket 连接，因此 30 秒
# 提供了舒适的保险余量。
HEARTBEAT_SECONDS = 30


# ============================================================================
# 主 WebSocket 处理器
# ============================================================================

async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    """
    WebSocket 端点，用于流式 Agent 交互。

    每个连接的生命周期：
        1. 接受握手，发送欢迎事件，启动心跳任务。
        2. 循环：接收用户消息，通过 astream_graph() 运行图，
           将每个 StreamEvent 以 JSON 格式分发给客户端。
        3. 断开连接 / 出错时，取消心跳，清理并关闭。

    参数：
        websocket: FastAPI WebSocket 连接。
        session_id: 从 URL 路径 /ws/{session_id} 中提取。
    """
    # --- 1. 接受连接 ---
    await websocket.accept()
    logger.info(f"WebSocket connected: session={session_id}")
    await websocket.send_json({
        "event": "connected",
        "data": {"session_id": session_id},
    })

    # --- 1b. 启动心跳任务 ---
    _stop_heartbeat = asyncio.Event()
    _heartbeat_task = asyncio.create_task(
        _heartbeat(websocket, _stop_heartbeat, session_id)
    )

    try:
        # --- 2. 消息循环 ---
        while True:
            raw = await websocket.receive_text()

            # 解析传入消息
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

            # --- 3. 构建初始状态 ---
            initial_state: AgentState = {
                "messages": [HumanMessage(content=message_text)],
                "session_id": session_id,
                "station_id": station_id,
            }

            # --- 4. 将图执行结果流式传输到 WebSocket ---
            t0 = time.monotonic()
            emitted_tokens: int = 0

            async for se in astream_graph(initial_state):
                # 将 StreamEvent 转换为 WebSocket JSON 消息
                ws_event = _stream_event_to_ws(se)

                # 追踪令牌数量用于最终摘要
                if se.type == "token" and se.content:
                    emitted_tokens += 1

                # 发送给客户端（同时充当隐式心跳）
                await websocket.send_json(ws_event)

            # --- 5. 最终摘要日志 ---
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
        # --- 清理：停止心跳 ---
        _stop_heartbeat.set()
        _heartbeat_task.cancel()
        try:
            await _heartbeat_task
        except asyncio.CancelledError:
            pass


# ============================================================================
# 心跳 — 防止浏览器/代理关闭空闲连接
# ============================================================================

async def _heartbeat(
    websocket: WebSocket,
    stop: asyncio.Event,
    session_id: str,
) -> None:
    """
    发送周期性 ping 以保持 WebSocket 连接活跃。

    一直运行直到 `stop` 被设置或 WebSocket 断开连接。
    """
    try:
        while not stop.is_set():
            await asyncio.sleep(HEARTBEAT_SECONDS)
            if not stop.is_set():
                await websocket.send_json({"event": "ping"})
                logger.debug(f"WS heartbeat: session={session_id}")
    except Exception:
        # 连接已关闭——预期行为，不记录为错误
        pass


# ============================================================================
# StreamEvent → WebSocket JSON 映射
# ============================================================================

def _stream_event_to_ws(se: Any) -> dict[str, Any]:
    """
    将 astream_graph() 中的 StreamEvent 转换为 WebSocket JSON 消息。

    事件类型映射：
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

    # 降级：未知事件类型
    return {
        "event": "unknown",
        "data": {"type": etype, "content": se.content or ""},
    }
