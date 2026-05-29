"""
Node: intent_router

对用户的自然语言查询做两件事：
1. LLM 分类意图（temperature=0，确定性输出）
2. 正则提取实体参数（station_id、年份、月份、指标名等）

提取的参数写入 state["query_params"]，供 tool_executor 透传给工具函数。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.schema import AgentState
from app.agent.events import emit, StreamEvent
from app.llm.provider import SYSTEM_PROMPT_CLASSIFICATION, ainvoke_with_retry
from loguru import logger


# ------------------------------------------------------------------
# 意图标签集合（白名单校验）
# ------------------------------------------------------------------
VALID_INTENTS: frozenset[str] = frozenset({
    "device_status",
    "power_curve",
    "fault_detection",
    "life_assessment",
    "report",
    "multi_station_summary",
    "general_chat",
})

# 需要工具执行的意图
TOOL_INTENTS: frozenset[str] = frozenset({
    "device_status",
    "power_curve",
    "fault_detection",
    "life_assessment",
    "report",
    "multi_station_summary",
})


# ------------------------------------------------------------------
# 实体提取（纯正则，走 LLM 之前先跑一次，结果是兜底）
# ------------------------------------------------------------------

def _resolve_relative_time(text: str) -> tuple[int | None, int | None, str | None, str | None]:
    """解析中文相对时间表达，返回 (year, month, start_date, end_date)。

    支持的表达:
        "今年" / "本年"     → 当前年
        "去年"             → 当前年 - 1
        "前年"             → 当前年 - 2
        "这个月" / "本月"   → 当前年 + 当前月
        "上个月"           → 当前月 - 1（跨年处理）
        "上季度"           → 3 个月前的日期范围
        "最近X天"          → X 天前到今天
        "最近一周"          → 7 天前到今天
    """
    now = datetime.now()
    year: int | None = None
    month: int | None = None
    start_date: str | None = None
    end_date: str | None = None

    text_lower = text.lower()

    # --- 绝对年份 "YYYY年" ---
    abs_year = re.search(r'(\d{4})年', text)
    if abs_year:
        # 绝对年份优先
        pass  # 在 _extract_entities 中处理
    else:
        # --- 相对年份 ---
        if re.search(r'前年', text):
            year = now.year - 2
        elif re.search(r'去年', text):
            year = now.year - 1
        elif re.search(r'(今年|本年)', text):
            year = now.year

    # --- 相对月份 ---
    if re.search(r'(这个月|本月)', text):
        month = now.month
        if year is None:
            year = now.year
    elif re.search(r'上个月', text):
        if now.month == 1:
            month = 12
            year = (year or now.year) - 1
        else:
            month = now.month - 1
            if year is None:
                year = now.year

    # --- 绝对月份 "M月"（仅在不是"X月X日"的模式下）---
    abs_month = re.search(r'(?<!\d)(\d{1,2})月(?!\d)', text)
    if abs_month and not month:
        m = int(abs_month.group(1))
        if 1 <= m <= 12:
            month = m

    # --- 相对日期范围 ---
    days_match = re.search(r'最近\s*(\d+)\s*天', text)
    if days_match:
        from datetime import timedelta
        days = int(days_match.group(1))
        end = now
        start = now - timedelta(days=days)
        start_date = start.strftime("%Y-%m-%d")
        end_date = end.strftime("%Y-%m-%d")
    elif re.search(r'最近一周', text):
        from datetime import timedelta
        end = now
        start = now - timedelta(days=7)
        start_date = start.strftime("%Y-%m-%d")
        end_date = end.strftime("%Y-%m-%d")
    elif re.search(r'上季度', text):
        current_q = (now.month - 1) // 3 + 1
        if current_q == 1:
            # Q4 of last year
            start_date = f"{now.year - 1}-10-01"
            end_date = f"{now.year - 1}-12-31"
        else:
            q_start_month = (current_q - 2) * 3 + 1
            start_date = f"{now.year}-{q_start_month:02d}-01"
            # last day of quarter
            q_end_month = q_start_month + 2
            end_date = f"{now.year}-{q_end_month:02d}-{30 if q_end_month != 12 else 31}"

    return year, month, start_date, end_date


def _extract_entities(text: str) -> dict:
    """从用户消息中用正则提取实体参数。

    支持:
        - 绝对时间: "2024年", "3月", "2024-01-01"
        - 相对时间: "去年", "今年", "前年", "上个月", "这个月", "最近一周", "上季度"
        - station_id: "X号电站" → "station-XXX"
        - metric: 发电量/效率/故障/健康度/容量因子
        - report_type: 年报/季度报告/故障报告
    """
    params: dict = {}

    # --- 相对时间解析 ---
    year, month, start_date, end_date = _resolve_relative_time(text)

    # --- 电站 ID ---
    station_match = re.search(r'(\d+)号电站', text)
    if station_match:
        num = station_match.group(1).zfill(3)
        params["station_id"] = f"station-{num}"

    # --- 年份（绝对年份优先于相对年份）---
    abs_year = re.search(r'(\d{4})年', text)
    if abs_year:
        params["year"] = int(abs_year.group(1))
    elif year is not None:
        params["year"] = year

    # --- 月份 ---
    if month is not None:
        params["month"] = month

    # --- 日期范围 ---
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date

    # 绝对日期范围 (YYYY-MM-DD)
    if not start_date or not end_date:
        dates = re.findall(r'(\d{4}-\d{2}-\d{2})', text)
        if len(dates) >= 2:
            params["start_date"] = dates[0]
            params["end_date"] = dates[1]
        elif len(dates) == 1 and not start_date:
            params["start_date"] = dates[0]

    # --- 指标类型 ---
    metric_map = {
        "发电量": "total_generation",
        "效率": "efficiency",
        "故障": "fault_rate",
        "健康度": "health_score",
        "容量因子": "capacity_factor",
    }
    for keyword, metric in metric_map.items():
        if keyword in text:
            params["metric"] = metric
            break

    # --- 报告类型 ---
    if "季度" in text or "Q1" in text.upper() or "Q2" in text.upper():
        params["report_type"] = "quarterly"
    elif "故障报告" in text or "事故报告" in text:
        params["report_type"] = "incident"
    elif "年报" in text or "年度" in text or "年" in text:
        params["report_type"] = "annual"

    return params


# ------------------------------------------------------------------
# 意图分类（LLM 调用）
# ------------------------------------------------------------------

def _parse_intent(raw: str) -> str:
    """清洗 LLM 返回的意图标签，做白名单校验。"""
    cleaned = raw.strip().lower().rstrip(".")
    # 去掉可能的引号和多余字符
    cleaned = cleaned.strip('"\'`*')

    if cleaned in VALID_INTENTS:
        return cleaned

    # 模糊匹配：如果包含某个有效意图名
    for intent in VALID_INTENTS:
        if intent in cleaned:
            return intent

    logger.warning(f"intent_router: unrecognized intent '{raw}', falling back to general_chat")
    return "general_chat"


async def intent_router(state: AgentState) -> AgentState:
    """分类用户意图 + 提取实体参数。

    数据流:
        入: state["messages"] (含当前用户消息)
        出: state["intent"], state["station_id"], state["query_params"]
    """
    await emit(StreamEvent(
        type="node_start", node="intent_router",
        message="正在分析您的查询意图...",
    ))

    messages = state.get("messages", [])

    # 提取最后一条用户消息
    last_user_msg: HumanMessage | None = None
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            last_user_msg = m
            break

    if last_user_msg is None:
        logger.warning("intent_router: no user message found, defaulting to general_chat")
        state["intent"] = "general_chat"
        state["query_params"] = {}
        return state

    user_text = last_user_msg.content
    if not isinstance(user_text, str):
        user_text = str(user_text)

    logger.debug(f"intent_router: classifying '{user_text[:80]}...'")

    # --- Step 1: 正则提取实体（先跑，不依赖 LLM） ---
    entities = _extract_entities(user_text)

    # --- Step 2: LLM 意图分类（含 retry：空响应 / ThinkingBlock / 网络错误）---
    try:
        raw_intent = await ainvoke_with_retry(
            [
                SystemMessage(content=SYSTEM_PROMPT_CLASSIFICATION),
                HumanMessage(content=user_text),
            ],
            temperature=0,
        )
        intent = _parse_intent(raw_intent)
    except Exception as e:
        logger.exception(f"intent_router: LLM classification failed after retries: {e}")
        intent = "general_chat"

    # --- Step 3: 写入 state ---
    state["intent"] = intent
    state["station_id"] = entities.pop("station_id", state.get("station_id"))
    state["query_params"] = entities

    logger.info(
        f"intent_router: intent={intent}, station={state['station_id']}, "
        f"params={entities}"
    )

    await emit(StreamEvent(
        type="node_complete", node="intent_router",
        intent=intent,
        message=f"意图识别: {intent}" + (f" (电站: {state['station_id']})" if state.get("station_id") else ""),
    ))

    return state


# ------------------------------------------------------------------
# 条件边函数
# ------------------------------------------------------------------

def route_after_intent(state: AgentState) -> Literal["needs_tools", "no_tools"]:
    """根据意图决定是否执行工具节点。"""
    intent = state.get("intent", "general_chat")
    if intent in TOOL_INTENTS:
        logger.debug(f"route_after_intent: {intent} → needs_tools")
        return "needs_tools"
    logger.debug(f"route_after_intent: {intent} → no_tools")
    return "no_tools"
