"""
Tool registration decorator.

Provides a @tool decorator that registers async functions as callable tools
in a global registry. Tools registered this way are automatically discoverable
by the tool_executor node based on the intent→tool mapping.

Usage:
    from app.tools import tool

    @tool(name="device_status", description="查询设备实时状态")
    async def query_device_status(station_id: str) -> dict:
        ...
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from loguru import logger

# Generic type for async tool functions
F = TypeVar("F", bound=Callable[..., Coroutine[Any, Any, Any]])

# Global tool registry: { tool_name: callable }
TOOL_REGISTRY: dict[str, Callable[..., Coroutine[Any, Any, Any]]] = {}


def tool(name: str, description: str = "") -> Callable[[F], F]:
    """
    Decorator that registers an async function as an invokable tool.

    Args:
        name: Unique tool identifier. Used as the key in TOOL_REGISTRY and
              referenced in the intent→tool mapping in tool_executor.
        description: Human-readable description of what the tool does.
                     Used in LLM prompts to describe available tools.

    Returns:
        The decorated function, unchanged, after registration.

    Example:
        @tool(name="power_curve", description="Retrieve power output curves")
        async def get_power_curve(station_id: str, start_date: str, end_date: str) -> dict:
            ...
    """

    def wrapper(fn: F) -> F:
        if name in TOOL_REGISTRY:
            logger.warning(f"Tool '{name}' is being overwritten in registry")

        TOOL_REGISTRY[name] = fn

        # Attach metadata to the function for introspection
        fn._tool_name = name  # type: ignore[attr-defined]
        fn._tool_description = description  # type: ignore[attr-defined]

        logger.debug(f"Registered tool: '{name}' — {description or 'no description'}")
        return fn

    return wrapper


def list_tools() -> list[dict[str, str]]:
    """
    Return a list of all registered tools with their metadata.

    Useful for building LLM tool-selection prompts and for introspection.

    Returns:
        List of dicts with keys: name, description.
    """
    return [
        {
            "name": name,
            "description": getattr(fn, "_tool_description", ""),
        }
        for name, fn in TOOL_REGISTRY.items()
    ]
