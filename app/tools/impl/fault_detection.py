"""
Tool: fault_detection

查询故障/告警记录——从 mock 数据层按 station_id + year 获取。
"""
from __future__ import annotations

from typing import Any

from app.data.mock import get_fault_data
from app.tools.decorator import tool
from loguru import logger


@tool(
    name="fault_detection",
    description="分析故障/告警日志，检测异常并分类故障类型",
)
async def fault_detection(
    station_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """查询指定电站某时间段的故障记录。

    Args:
        station_id: 电站 ID。
        start_date: ISO 日期 "2024-01-01"。
        end_date: ISO 日期 "2024-12-31"。
    """
    logger.info(
        f"fault_detection: station={station_id}, "
        f"range={start_date} to {end_date}"
    )
    return get_fault_data(station_id, start_date, end_date)
