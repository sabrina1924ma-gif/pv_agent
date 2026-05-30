"""
工具注册装饰器。

提供 @tool 装饰器，将异步函数注册为全局注册表中的可调用工具。
通过此方式注册的工具可被 tool_executor 节点基于 intent→tool 映射自动发现。

用法:
    from app.tools import tool

    @tool(name="device_status", description="查询设备实时状态")
    async def query_device_status(station_id: str) -> dict:
        ...
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from loguru import logger

# 异步工具函数的通用类型
F = TypeVar("F", bound=Callable[..., Coroutine[Any, Any, Any]])

# 全局工具注册表：{ tool_name: callable }
TOOL_REGISTRY: dict[str, Callable[..., Coroutine[Any, Any, Any]]] = {}


def tool(name: str, description: str = "") -> Callable[[F], F]:
    """
    装饰器，将异步函数注册为可调用的工具。

    Args:
        name: 唯一工具标识符。用作 TOOL_REGISTRY 中的键，
              并在 tool_executor 的 intent→tool 映射中引用。
        description: 工具功能的人类可读描述。
                     用于 LLM 提示词中描述可用工具。

    Returns:
        注册后的装饰函数，保持不变。

    示例:
        @tool(name="power_curve", description="查询历史发电功率曲线")
        async def get_power_curve(station_id: str, start_date: str, end_date: str) -> dict:
            ...
    """

    def wrapper(fn: F) -> F:
        if name in TOOL_REGISTRY:
            logger.warning(f"Tool '{name}' is being overwritten in registry")

        TOOL_REGISTRY[name] = fn

        # 将元数据附加到函数上，用于内省
        fn._tool_name = name  # type: ignore[attr-defined]
        fn._tool_description = description  # type: ignore[attr-defined]
        return fn

    return wrapper


def list_tools() -> list[dict[str, str]]:
    """
    返回所有已注册工具及其元数据的列表。

    用于构建 LLM 工具选择提示词和内省。

    Returns:
        包含 name、description 键的字典列表。
    """
    return [
        {
            "name": name,
            "description": getattr(fn, "_tool_description", ""),
        }
        for name, fn in TOOL_REGISTRY.items()
    ]
