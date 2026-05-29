"""
Tool: power_curve

查询历史发电功率曲线——按 station_id + year 从 mock 数据层获取。
"""
from __future__ import annotations

from typing import Any

from app.data.mock import get_power_data
from app.tools.decorator import tool
from loguru import logger


@tool(
    name="power_curve",
    description="查询历史发电功率曲线数据，支持按年/月/日粒度",
)
async def power_curve(
    station_id: str,
    year: int | None = None,
    month: int | None = None,
    resolution: str = "daily",
) -> dict[str, Any]:
    """获取指定电站的历史发电数据。按 station_id + year 筛选。

    Args:
        station_id: 电站 ID。
        year: 年份（默认当年）。
        month: 月份 1-12。
        resolution: 粒度 "hourly"|"daily"|"monthly"。
    """
    year = year or 2024
    logger.info(
        f"power_curve: station={station_id}, year={year}, "
        f"month={month}, resolution={resolution}"
    )

    result = get_power_data(station_id, year, month, resolution)

    # 如果只查单月，过滤数据
    if month and result["data"]:
        result["data"] = [result["data"][month - 1]]

    return result
