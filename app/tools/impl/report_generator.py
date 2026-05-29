"""
Tool: report_generator

聚合电站多维度数据为结构化报告负载——从 mock 数据层汇总。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.data.mock import get_station, get_power_data, get_fault_data, get_life_assessment as mock_life
from app.tools.decorator import tool
from loguru import logger


@tool(
    name="report_generator",
    description="聚合电站多维度数据，生成结构化报告负载",
)
async def report_generator(
    station_id: str,
    year: int | None = None,
    report_type: str = "annual",
) -> dict[str, Any]:
    """聚合电站发电、故障、健康三维度数据。

    Args:
        station_id: 电站 ID。
        year: 报告年份。
        report_type: "annual"|"quarterly"|"incident"。
    """
    year = year or 2024
    logger.info(
        f"report_generator: station={station_id}, year={year}, type={report_type}"
    )

    station = get_station(station_id)
    if not station:
        return {
            "station_id": station_id, "station_name": f"Unknown ({station_id})",
            "report_type": report_type, "year": year,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sections": {},
            "error": f"Station {station_id} not found",
            "queried_params": {"station_id": station_id, "year": year,
                              "report_type": report_type, "source": "mock"},
        }

    power = get_power_data(station_id, year)
    faults = get_fault_data(station_id, f"{year}-01-01", f"{year}-12-31")
    life = mock_life(station_id)

    return {
        "station_id": station_id,
        "station_name": station["name"],
        "report_type": report_type,
        "year": year,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sections": {
            "overview": {
                "installed_capacity_kw": station["capacity_kw"],
                "annual_generation_kwh": power["summary"]["total_kwh"],
                "capacity_factor": round(
                    power["summary"]["total_kwh"] / (station["capacity_kw"] * 365 * 24), 3
                ),
                "operating_days": 362,
                "grid_connection_date": station["grid_connection_date"],
            },
            "generation": {
                "total_kwh": power["summary"]["total_kwh"],
                "monthly_breakdown": [
                    {"month": i + 1, "kwh": d["energy_kwh"]}
                    for i, d in enumerate(power["data"])
                ],
                "peak_month": power["data"].index(
                    max(power["data"], key=lambda d: d["energy_kwh"])
                ) + 1,
                "peak_value_kwh": power["summary"]["peak_value_kwh"],
                "trend": power["trend"],
            },
            "faults": {
                "total_count": faults["total_faults"],
                "by_severity": faults["by_severity"],
                "top_codes": list(set(
                    f["code"][:7] for f in faults["fault_log"]
                ))[:3],
            },
            "health": {
                "overall_score": life["overall_health_score"],
                "components_at_risk": life["critical_components"],
                "recommendation": life["recommendation"],
            },
        },
        "queried_params": {
            "station_id": station_id, "year": year,
            "report_type": report_type, "source": "mock",
        },
    }
