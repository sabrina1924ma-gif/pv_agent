"""
Tool: device_status

查询设备/电站的实时运行状态——由 mock 数据层按 station_id 返回不同数据。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.data.mock import get_station, get_power_data
from app.tools.decorator import tool
from loguru import logger


@tool(
    name="device_status",
    description="查询设备/电站的实时运行状态和即时指标",
)
async def device_status(station_id: str) -> dict[str, Any]:
    """获取指定电站的当前运行快照。

    实时指标从该电站当月发电数据推导，告警从故障数据抽样。

    Args:
        station_id: 电站唯一标识符。
    """
    station = get_station(station_id)
    if not station:
        logger.warning(f"device_status: station {station_id} not found")
        return {
            "station_id": station_id,
            "station_name": f"Unknown ({station_id})",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "offline",
            "metrics": {},
            "alarms": [],
            "last_maintenance": "N/A",
            "error": f"Station {station_id} not found",
            "queried_params": {"station_id": station_id, "source": "mock"},
        }

    now = datetime.now(timezone.utc)
    month = now.month
    year = now.year

    # 用当月发电数据推导实时功率（取日均 ÷ 日照小时数 ≈ 瞬时功率）
    power_data = get_power_data(station_id, year, month)
    daily_kwh = power_data["summary"]["daily_avg_kwh"]
    current_power = round(daily_kwh / 5.5, 1)  # 5.5 日照小时

    # 故障数据抽样告警
    from app.data.mock import get_fault_data
    fault_data = get_fault_data(station_id, f"{year}-01-01", f"{year}-12-31")
    unresolved = [f for f in fault_data["fault_log"] if not f["resolved"]]
    alarms = [
        {"code": f["code"], "level": f["severity"], "message": f["description"]}
        for f in unresolved[:3]
    ]

    logger.info(f"device_status: station={station_id}, power={current_power}kW, alarms={len(alarms)}")

    return {
        "station_id": station_id,
        "station_name": station["name"],
        "timestamp": now.isoformat(),
        "status": "warning" if len(alarms) >= 1 else "running",
        "metrics": {
            "current_power_kw": current_power,
            "daily_energy_kwh": daily_kwh,
            "efficiency": round(0.88 + (station["health_base"] - 0.78) * 0.3, 2),
            "temperature_c": round(35 + (month - 6) * 3 + (6 - abs(month - 6)) * 2, 1),
            "irradiance_wm2": round(600 + (6 - abs(month - 6)) * 150, 1),
        },
        "alarms": alarms,
        "last_maintenance": f"{year - 1}-12-15T10:00:00+08:00"
                           if month > 3 else f"{year}-01-10T10:00:00+08:00",
        "queried_params": {"station_id": station_id, "source": "mock"},
    }
