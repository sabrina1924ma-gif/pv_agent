"""
Tool: multi_station_summary

跨电站对比分析——从 mock 数据层汇总多个电站数据。
"""
from __future__ import annotations

from typing import Any

from app.data.mock import STATIONS, get_power_data, get_life_assessment as mock_life
from app.data.mock import get_fault_data
from app.tools.decorator import tool
from loguru import logger


@tool(
    name="multi_station_summary",
    description="跨多个电站聚合和对比关键指标，提供排名和横向分析",
)
async def multi_station_summary(
    station_ids: list[str],
    metric: str = "total_generation",
    year: int | None = None,
) -> dict[str, Any]:
    """对比多个电站在指定年份的核心指标。

    Args:
        station_ids: 电站 ID 列表。
        metric: 对比指标 "total_generation"|"efficiency"|"fault_rate"|"health_score"|"capacity_factor"。
        year: 年份。
    """
    year = year or 2024
    logger.info(
        f"multi_station_summary: {len(station_ids)} stations, "
        f"metric={metric}, year={year}"
    )

    stations_data = []
    for sid in station_ids:
        if sid not in STATIONS:
            continue
        st = STATIONS[sid]
        power = get_power_data(sid, year)
        life = mock_life(sid)
        faults = get_fault_data(sid, f"{year}-01-01", f"{year}-12-31")
        stations_data.append({
            "station_id": sid,
            "station_name": st["name"],
            "capacity_kw": st["capacity_kw"],
            "total_kwh": power["summary"]["total_kwh"],
            "capacity_factor": round(
                power["summary"]["total_kwh"] / (st["capacity_kw"] * 365 * 24), 3
            ),
            "efficiency": round(0.88 + (st["health_base"] - 0.78) * 0.3, 2),
            "fault_count": faults["total_faults"],
            "health_score": life["overall_health_score"],
        })

    by_gen = sorted(stations_data, key=lambda s: s["total_kwh"], reverse=True)
    by_health = sorted(stations_data, key=lambda s: s["health_score"], reverse=True)
    by_eff = sorted(stations_data, key=lambda s: s["efficiency"], reverse=True)

    total_gen = sum(s["total_kwh"] for s in stations_data)

    return {
        "period": {"year": year, "start": f"{year}-01-01", "end": f"{year}-12-31"},
        "metric": metric,
        "station_count": len(stations_data),
        "stations": stations_data,
        "ranking": {
            "by_generation": [s["station_id"] for s in by_gen],
            "by_efficiency": [s["station_id"] for s in by_eff],
            "by_health": [s["station_id"] for s in by_health],
        },
        "aggregate": {
            "total_generation_kwh": total_gen,
            "avg_health_score": round(
                sum(s["health_score"] for s in stations_data) / len(stations_data), 2
            ),
            "total_fault_count": sum(s["fault_count"] for s in stations_data),
        },
        "highlights": [
            {
                "label": "发电量领先",
                "detail": f"{by_gen[0]['station_name']} 发电 {by_gen[0]['total_kwh']/10000:.1f} 万 kWh，位居第一",
            },
            {
                "label": "健康度预警",
                "detail": f"{by_health[-1]['station_name']} 健康度 {by_health[-1]['health_score']}，需重点关注",
            },
            {
                "label": "总览",
                "detail": f"{len(stations_data)} 座电站总发电 {total_gen/10000:.1f} 万 kWh",
            },
        ],
        "comparison_notes": (
            f"发电量: {by_gen[0]['station_name']}>{by_gen[1]['station_name']}>...。"
            f"健康度: {by_health[0]['station_name']} 最优。"
            f"故障数: {by_health[-1]['station_name']} 最多 ({by_health[-1]['fault_count']} 次)。"
        ),
        "queried_params": {
            "station_ids": station_ids, "year": year,
            "metric": metric, "source": "mock",
        },
    }
