"""
流式事件定义和上下文传递。

独立于 graph.py，避免循环导入。
节点和 streaming.py 都从这个模块导入 StreamEvent 和 emit。
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from loguru import logger


# ============================================================================
# 事件定义
# ============================================================================

@dataclass
class StreamEvent:
    """流式事件。类型:
    thinking / node_start / node_complete / tool_start / tool_complete / token / done / error
    """
    type: str
    content: str = ""
    node: str = ""
    tool: str = ""
    intent: str = ""
    message: str = ""
    elapsed_ms: float = 0


# ============================================================================
# 上下文传递（contextvars → 协程安全，不侵入函数签名）
# ============================================================================

StreamCallback = Callable[[StreamEvent], Coroutine[Any, Any, None]]

_stream_callback: contextvars.ContextVar[StreamCallback | None] = (
    contextvars.ContextVar("stream_callback", default=None)
)


def set_stream_callback(cb: StreamCallback | None) -> None:
    """设置当前协程上下文的流式回调。"""
    _stream_callback.set(cb)


def get_stream_callback() -> StreamCallback | None:
    """获取当前协程上下文的流式回调。"""
    return _stream_callback.get(None)


async def emit(event: StreamEvent) -> None:
    """发送事件到当前上下文的回调（无回调时静默跳过）。"""
    cb = get_stream_callback()
    if cb:
        try:
            await cb(event)
        except Exception:
            logger.exception("events: callback failed")
