"""
Tool: life_assessment

评估设备健康状态和剩余寿命——从 mock 数据层按 station_id 获取。
"""
from __future__ import annotations

from typing import Any

from app.data.mock import get_life_assessment as mock_life
from app.tools.decorator import tool
from loguru import logger


@tool(
    name="life_assessment",
    description="评估设备剩余使用寿命和健康状态",
)
async def life_assessment(station_id: str) -> dict[str, Any]:
    """评估指定电站各核心组件的健康状态。

    Args:
        station_id: 电站 ID。
    """
    logger.info(f"life_assessment: station={station_id}")
    return mock_life(station_id)
