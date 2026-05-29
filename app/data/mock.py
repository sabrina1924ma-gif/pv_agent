"""
光伏电站 Mock 数据层。

5 座电站 × 3 年（2024-2026）× 月粒度发电数据 + 故障记录 + 设备健康评估。
所有数据按 station_id / year 筛选返回——不再是硬编码的单一数据集。
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any

# ============================================================================
# 电站基础信息
# ============================================================================

STATIONS: dict[str, dict[str, Any]] = {
    "station-001": {
        "name": "1号电站",
        "capacity_kw": 5000,
        "location": "甘肃敦煌",
        "grid_connection_date": "2019-03-15",
        "panel_model": "JKM550M-72HL4",
        "inverter_model": "Sungrow SG125HV",
        "inverter_count": 8,
        "description": "大型地面电站，沙漠戈壁地貌，光照资源丰富但沙尘影响大",
        # 发电量季节系数：春(0.75) 夏(0.95) 秋(0.70) 冬(0.45)
        # × 容量系数 0.22 → 月度基线
        "seasonal_factors": [0.65, 0.72, 0.85, 0.95, 1.00, 1.05,
                            1.08, 1.02, 0.88, 0.75, 0.62, 0.50],
        "base_monthly_kwh": 80000,  # 月度基准
        "fault_rate": 0.6,          # 故障频率系数
        "health_base": 0.90,        # 基础健康度
    },
    "station-002": {
        "name": "2号电站",
        "capacity_kw": 3000,
        "location": "江苏盐城",
        "grid_connection_date": "2020-06-01",
        "panel_model": "LONGi LR5-72HBD",
        "inverter_model": "Huawei SUN2000-100KTL",
        "inverter_count": 6,
        "description": "渔光互补电站，水面光伏，湿度大但散热好，效率偏高",
        "seasonal_factors": [0.60, 0.68, 0.80, 0.90, 0.95, 1.00,
                            1.05, 1.08, 0.95, 0.82, 0.68, 0.55],
        "base_monthly_kwh": 55000,
        "fault_rate": 0.3,
        "health_base": 0.94,
    },
    "station-003": {
        "name": "3号电站",
        "capacity_kw": 5000,
        "location": "青海海南州",
        "grid_connection_date": "2020-06-01",
        "panel_model": "JKM550M-72HL4",
        "inverter_model": "Sungrow SG125HV",
        "inverter_count": 8,
        "description": "高原电站，海拔 3200m，辐照强但逆变器故障率偏高",
        "seasonal_factors": [0.68, 0.75, 0.88, 0.92, 0.98, 1.02,
                            1.05, 1.00, 0.90, 0.78, 0.65, 0.55],
        "base_monthly_kwh": 78000,
        "fault_rate": 0.9,
        "health_base": 0.82,
    },
    "station-004": {
        "name": "4号电站",
        "capacity_kw": 2000,
        "location": "浙江宁波",
        "grid_connection_date": "2021-09-01",
        "panel_model": "Trina TSM-DE21",
        "inverter_model": "Huawei SUN2000-50KTL",
        "inverter_count": 8,
        "description": "分布式工商业屋顶电站，运行稳定，故障极少",
        "seasonal_factors": [0.55, 0.62, 0.75, 0.85, 0.92, 0.98,
                            1.05, 1.08, 0.95, 0.80, 0.62, 0.52],
        "base_monthly_kwh": 35000,
        "fault_rate": 0.15,
        "health_base": 0.96,
    },
    "station-005": {
        "name": "5号电站",
        "capacity_kw": 4000,
        "location": "河北张家口",
        "grid_connection_date": "2018-11-20",
        "panel_model": "JKM550M-72HL4",
        "inverter_model": "Sungrow SG125HV",
        "inverter_count": 6,
        "description": "山地电站，地形复杂，冬季积雪影响大，设备老化较明显",
        "seasonal_factors": [0.55, 0.65, 0.78, 0.88, 0.95, 1.00,
                            1.02, 0.98, 0.85, 0.72, 0.55, 0.40],
        "base_monthly_kwh": 65000,
        "fault_rate": 0.7,
        "health_base": 0.78,
    },
}


# ============================================================================
# 基础查询函数
# ============================================================================

def get_station(station_id: str) -> dict[str, Any] | None:
    """获取电站基本信息。"""
    return STATIONS.get(station_id)


def get_power_data(
    station_id: str,
    year: int,
    month: int | None = None,
    resolution: str = "daily",
) -> dict[str, Any]:
    """按电站和年份获取发电数据。

    根据电站的季节系数和基准发电量生成 12 个月的模拟数据。
    年份不同会有 ±2% 的随机偏移模拟年际变化。
    """
    station = STATIONS.get(station_id)
    if not station:
        return _empty_power_result(station_id, year, month, resolution)

    # 年份偏移：模拟年际变化（组件衰减 + 天气波动）
    rng = random.Random(f"{station_id}-{year}")
    year_offset = 1.0 + rng.uniform(-0.03, 0.03)
    # 年衰减：每年衰减 0.5%
    decay = 1.0 - (year - 2024) * 0.005

    factors = station["seasonal_factors"]
    base = station["base_monthly_kwh"]

    monthly_data = []
    for m in range(12):
        m_kwh = round(base * factors[m] * year_offset * decay, 1)
        # 加日波动噪声
        noise = rng.uniform(-0.05, 0.05)
        m_kwh = round(m_kwh * (1 + noise), 1)
        peak_kw = round(station["capacity_kw"] * factors[m] * 1.1, 1)
        monthly_data.append({
            "date": f"{year}-{m + 1:02d}-01",
            "energy_kwh": m_kwh,
            "peak_power_kw": peak_kw,
        })

    total = sum(d["energy_kwh"] for d in monthly_data)
    peak_month = max(monthly_data, key=lambda d: d["energy_kwh"])
    min_month = min(monthly_data, key=lambda d: d["energy_kwh"])

    return {
        "station_id": station_id,
        "station_name": station["name"],
        "year": year,
        "month": month,
        "resolution": resolution,
        "unit": "kWh",
        "capacity_kw": station["capacity_kw"],
        "data": monthly_data,
        "summary": {
            "total_kwh": round(total, 1),
            "daily_avg_kwh": round(total / 365, 1),
            "peak_day": peak_month["date"],
            "peak_value_kwh": peak_month["energy_kwh"],
            "min_day": min_month["date"],
            "min_value_kwh": min_month["energy_kwh"],
        },
        "trend": _trend_label(monthly_data),
        "queried_params": {
            "station_id": station_id,
            "year": year,
            "month": month,
            "resolution": resolution,
            "source": "mock",
        },
    }


def get_fault_data(
    station_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """按电站获取故障记录。故障数量与 station.fault_rate 成正比。"""
    station = STATIONS.get(station_id)
    if not station:
        return _empty_fault_result(station_id, start_date, end_date)

    year = int((start_date or "2024-01-01")[:4])
    rng = random.Random(f"{station_id}-faults-{year}")

    # 故障数量：容量 × 故障率 ÷ 1000，至少 1 条
    fault_count = max(1, int(station["capacity_kw"] * station["fault_rate"] / 1000))
    severities = ["critical", "major", "minor"]
    severity_weights = [0.1, 0.3, 0.6]

    fault_codes = [
        ("INV-OV", "逆变器过压"),
        ("INV-TEMP", "逆变器超温"),
        ("STR-COMM", "汇流箱通讯中断"),
        ("PV-ISO", "组串绝缘阻抗低"),
        ("GRID-FREQ", "电网频率异常"),
        ("INV-DC", "直流侧过流"),
        ("TRACK-ERR", "跟踪支架故障"),
        ("CLEAN-REQ", "组件清洁度告警"),
    ]

    fault_log = []
    by_sev = {"critical": 0, "major": 0, "minor": 0}
    for i in range(fault_count):
        sev = rng.choices(severities, weights=severity_weights, k=1)[0]
        by_sev[sev] += 1
        code_idx = rng.randint(0, len(fault_codes) - 1)
        code, desc = fault_codes[code_idx]
        month = rng.randint(1, 12)
        day = rng.randint(1, 28)
        hour = rng.randint(6, 20)
        duration = rng.randint(5, 240)
        resolved = rng.random() > 0.15

        fault_log.append({
            "id": f"FL-{year}{month:02d}{day:02d}-{i + 1:03d}",
            "timestamp": f"{year}-{month:02d}-{day:02d}T{hour:02d}:00:00+08:00",
            "severity": sev,
            "code": f"{code}-{rng.randint(1, 99):03d}",
            "component": f"逆变器#{rng.randint(1, station['inverter_count'])}"
                        if "INV" in code else "汇流箱",
            "description": desc,
            "duration_minutes": duration,
            "resolved": resolved,
            "resolution": "自动复位" if resolved else "待处理",
        })

    fault_log.sort(key=lambda f: f["timestamp"])

    station_name = station["name"]
    return {
        "station_id": station_id,
        "station_name": station_name,
        "period": {
            "start": start_date or f"{year}-01-01",
            "end": end_date or f"{year}-12-31",
        },
        "total_faults": fault_count,
        "by_severity": by_sev,
        "fault_log": fault_log,
        "summary": (
            f"{year}年{station_name}共发生 {fault_count} 次故障，"
            f"严重 {by_sev['critical']} 次、主要 {by_sev['major']} 次、"
            f"轻微 {by_sev['minor']} 次。"
        ),
        "queried_params": {
            "station_id": station_id,
            "start_date": start_date,
            "end_date": end_date,
            "source": "mock",
        },
    }


def get_life_assessment(station_id: str) -> dict[str, Any]:
    """按电站获取设备健康评估。健康度 = station.health_base ± 噪声。"""
    station = STATIONS.get(station_id)
    if not station:
        return _empty_life_result(station_id)

    rng = random.Random(f"life-{station_id}")
    base_health = station["health_base"]
    name = station["name"]

    def _component(name_cn: str, model: str, design_life: int,
                   age_base: float, degradation: float,
                   inverters: int = 1) -> dict:
        health = round(base_health + rng.uniform(-0.08, 0.05), 2)
        health = max(0.1, min(1.0, health))
        age = age_base + rng.uniform(-0.5, 0.5)
        remaining = max(0, design_life - age - rng.uniform(0, 2))
        if health >= 0.85:
            status = "healthy"
        elif health >= 0.70:
            status = "warning"
        elif health >= 0.50:
            status = "at_risk"
        else:
            status = "critical"

        count_str = f"×{inverters}" if inverters > 1 else ""
        return {
            "name": f"{name_cn}{count_str}",
            "model": model,
            "install_date": station["grid_connection_date"],
            "design_life_years": design_life,
            "age_years": round(age, 1),
            "degradation_rate_pct_per_year": degradation,
            "current_efficiency_pct": round(100 - age * degradation, 1),
            "health_score": health,
            "estimated_remaining_years": round(remaining, 1),
            "status": status,
            "note": None if health >= 0.85 else (
                "建议加强巡检" if health >= 0.70 else "建议安排预防性维护/更换"
            ),
        }

    components = [
        _component("光伏组件", station["panel_model"], 25, 5.0, 0.55),
        _component("逆变器", station["inverter_model"], 15, 5.0, 2.0,
                   inverters=station["inverter_count"]),
        _component("变压器 T1", "TBEA S11-1600/35", 30, 5.0, 0.3),
    ]

    overall = round(sum(c["health_score"] for c in components) / len(components), 2)
    at_risk = [c["name"] for c in components if c["status"] in ("at_risk", "critical")]

    return {
        "station_id": station_id,
        "station_name": station["name"],
        "assessment_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "components": components,
        "overall_health_score": overall,
        "critical_components": at_risk,
        "recommendation": (
            f"{name}整体健康度 {overall}。"
            + (f"重点关注: {', '.join(at_risk)}。" if at_risk
               else "所有组件运行正常，按常规计划巡检。")
        ),
        "queried_params": {
            "station_id": station_id,
            "source": "mock",
        },
    }


# ============================================================================
# 辅助函数
# ============================================================================

def _trend_label(data: list[dict]) -> str:
    """简单趋势判断：比较前后半年的均值。"""
    if len(data) < 6:
        return "stable"
    first_half = sum(d["energy_kwh"] for d in data[:6])
    second_half = sum(d["energy_kwh"] for d in data[6:])
    diff_pct = (second_half - first_half) / first_half * 100 if first_half else 0
    if diff_pct > 5:
        return "upward"
    elif diff_pct < -5:
        return "downward"
    return "stable"


def _empty_power_result(station_id: str, year: int,
                        month: int | None, resolution: str) -> dict:
    return {
        "station_id": station_id,
        "station_name": f"Unknown ({station_id})",
        "year": year,
        "month": month,
        "resolution": resolution,
        "unit": "kWh",
        "capacity_kw": 0,
        "data": [],
        "summary": {"total_kwh": 0, "daily_avg_kwh": 0,
                    "peak_day": "", "peak_value_kwh": 0,
                    "min_day": "", "min_value_kwh": 0},
        "trend": "stable",
        "queried_params": {"station_id": station_id, "year": year,
                          "month": month, "resolution": resolution, "source": "mock"},
        "error": f"Station {station_id} not found in mock data",
    }


def _empty_fault_result(station_id: str,
                        start_date: str | None,
                        end_date: str | None) -> dict:
    return {
        "station_id": station_id,
        "station_name": f"Unknown ({station_id})",
        "period": {"start": start_date or "?", "end": end_date or "?"},
        "total_faults": 0,
        "by_severity": {"critical": 0, "major": 0, "minor": 0},
        "fault_log": [],
        "summary": f"Station {station_id} not found.",
        "queried_params": {"station_id": station_id, "start_date": start_date,
                          "end_date": end_date, "source": "mock"},
    }


def _empty_life_result(station_id: str) -> dict:
    return {
        "station_id": station_id,
        "station_name": f"Unknown ({station_id})",
        "assessment_date": "",
        "components": [],
        "overall_health_score": 0,
        "critical_components": [],
        "recommendation": f"Station {station_id} not found.",
        "queried_params": {"station_id": station_id, "source": "mock"},
    }
