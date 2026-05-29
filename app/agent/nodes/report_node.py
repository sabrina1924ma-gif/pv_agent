"""
Node: report_node

聚合全量上下文（用户查询、意图、工具结果）→ 调 LLM 生成 Markdown 报告。

防幻觉四层机制:
    1. System Prompt 硬约束：只输出 tool_results 中已有的数据
    2. 强制末尾"数据校验"段：LLM 必须列出数据来源
    3. 后置校验函数 _validate_report：检测报告中出现的年份是否在 tool_results 范围内
    4. 校验失败时追加警告标记
"""

from __future__ import annotations

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.schema import AgentState
from app.agent.events import emit, StreamEvent
from app.config import get_settings
from app.llm.provider import astream_with_retry
from loguru import logger


# ============================================================================
# Token 缓冲器——避免逐字符发射事件
# ============================================================================

class TokenBuffer:
    """攒够一定字符或遇到换行时才发射一个 token 事件。

    避免 LLM astream 逐字符输出时前端收到大量单字事件。
    首个 chunk 立刻发射以保证首响应 <500ms，后续走缓冲。
    """

    def __init__(self, min_chars: int = 20):
        self._buf: list[str] = []
        self._min_chars = min_chars
        self._first = True

    async def feed(self, token: str) -> None:
        """喂入一个 token。达到阈值或遇到换行时自动发射。"""
        self._buf.append(token)
        combined = "".join(self._buf)

        # 首个 chunk 立刻发射以保首响应 <500ms
        if self._first:
            self._first = False
            await self.flush()
        # 遇到换行 → 立即发射
        elif "\n" in combined:
            await self.flush()
        # 攒够最小字符数 → 发射
        elif len(combined) >= self._min_chars:
            await self.flush()

    async def flush(self) -> None:
        """强制发射缓冲区中所有内容。"""
        if self._buf:
            combined = "".join(self._buf)
            self._buf.clear()
            if combined:
                await emit(StreamEvent(type="token", content=combined))


# ============================================================================
# 报告生成 System Prompt（强化版——含日期验证约束）
# ============================================================================

# ============================================================================
# 系统提示词：短人格（每次请求都用，控制 TTFT）
# ============================================================================

PERSONA_PROMPT = """\
你叫小光，是光伏电站智能运维助手。说话像可靠的同事——亲切自然、先给结论再给细节。你熟悉光伏发电、设备故障、健康评估等专业领域，但用白话把事说清楚。
核心操守：只输出数据里有的内容，绝不编造数字或日期。数据不足就坦诚说明，工具报错就如实告知。日常聊天时用轻松语气回应并引导到正事。
"""

# ============================================================================
# 报告约束（仅在数据报告时追加到 User prompt 尾部）
# ============================================================================

REPORT_CONSTRAINTS = """\
## 额外约束（数据报告专用）

1. 报告结构：一句话总结 → 关键指标(Markdown表格) → 详细分析 → 建议 → 数据校验
2. 数字原样照抄工具结果，不四舍五入不换算。年份不匹配要明确告知。
3. 末尾必须有"## 数据校验"段，列出数据来源、查询年份、确认3-5个关键数字一致。
"""


# ============================================================================
# Prompt 构建
# ============================================================================

def _compress_tool_results(tool_results: list[dict]) -> list[dict]:
    """压缩工具结果为摘要形式，大幅削减 prompt token 数以降低 TTFT。

    只保留 LLM 生成报告必需的摘要字段，丢弃：
    - 完整的 data 数组（如 12 个月的逐日明细）
    - 重复的 queried_params
    - 组件级的详细故障和健康数据
    """
    compressed = []
    for r in tool_results:
        entry: dict = {
            "tool": r.get("tool_name", "unknown"),
            "status": r.get("status", "unknown"),
        }
        if r.get("status") != "ok" or not r.get("data"):
            if r.get("status") == "error":
                entry["error"] = r.get("error", "Unknown error")
            compressed.append(entry)
            continue

        data = r["data"]
        if not isinstance(data, dict):
            entry["data"] = str(data)[:200]
            compressed.append(entry)
            continue

        # --- power_curve / device_status: 保留 summary + 趋势，去掉数组 ---
        if "summary" in data:
            entry["data"] = {
                "station_name": data.get("station_name", ""),
                "year": data.get("year"),
                "capacity_kw": data.get("capacity_kw"),
                "summary": data["summary"],
                "trend": data.get("trend", ""),
            }
            # 如果只有一个月的数据（基本就是 12 个元素），给出月度概览而非逐条明细
            raw_data = data.get("data", [])
            if isinstance(raw_data, list) and 6 <= len(raw_data) <= 14:
                entry["data"]["monthly_kwh"] = [
                    round(d["energy_kwh"]) for d in raw_data if isinstance(d, dict)
                ]
            # device_status 有 metrics 和 alarms
            if "metrics" in data:
                entry["data"]["metrics"] = data["metrics"]
            if "alarms" in data:
                entry["data"]["alarm_count"] = len(data.get("alarms", []))
            compressed.append(entry)
            continue

        # --- fault_detection: 保留统计，去掉 fault_log 数组 ---
        if "fault_log" in data:
            entry["data"] = {
                "station_name": data.get("station_name", ""),
                "period": data.get("period", {}),
                "total_faults": data.get("total_faults"),
                "by_severity": data.get("by_severity"),
                "summary": data.get("summary", ""),
            }
            compressed.append(entry)
            continue

        # --- life_assessment: 保留 overall，去掉 components 数组 ---
        if "components" in data:
            entry["data"] = {
                "station_name": data.get("station_name", ""),
                "overall_health_score": data.get("overall_health_score"),
                "critical_components": data.get("critical_components", []),
                "recommendation": data.get("recommendation", ""),
            }
            compressed.append(entry)
            continue

        # --- report_generator: 保留 sections 但压缩 generation.monthly_breakdown ---
        if "sections" in data:
            entry["data"] = {
                "station_name": data.get("station_name", ""),
                "year": data.get("year"),
                "report_type": data.get("report_type"),
                "sections": _compress_sections(data["sections"]),
            }
            compressed.append(entry)
            continue

        # --- multi_station_summary: 保留汇总表，去掉每个站的组件明细 ---
        if "stations" in data:
            slim_stations = []
            for s in data.get("stations", []):
                slim_stations.append({
                    "station_name": s.get("station_name", ""),
                    "capacity_kw": s.get("capacity_kw"),
                    "total_generation_kwh": s.get("total_generation_kwh"),
                    "health_score": s.get("health_score"),
                    "fault_count": s.get("fault_count"),
                })
            entry["data"] = {
                "stations": slim_stations,
                "rankings": data.get("rankings", {}),
                "highlights": data.get("highlights", ""),
            }
            compressed.append(entry)
            continue

        # 兜底：截断 data
        entry["data"] = str(data)[:500]
        compressed.append(entry)

    return compressed


def _compress_sections(sections: dict) -> dict:
    """压缩报告 sections，把月度明细替换为月度数值列表。"""
    result = {}
    for key, val in sections.items():
        if key == "generation" and isinstance(val, dict):
            gen = dict(val)
            if "monthly_breakdown" in gen:
                breakdown = gen.pop("monthly_breakdown")
                gen["monthly_kwh"] = [
                    round(m["kwh"]) for m in breakdown if isinstance(m, dict)
                ]
                gen["month_count"] = len(breakdown)
            result[key] = gen
        elif key == "faults" and isinstance(val, dict):
            f = dict(val)
            f.pop("top_codes", None)
            result[key] = f
        elif key == "health" and isinstance(val, dict):
            result[key] = val
        else:
            result[key] = val
    return result


def _build_generation_prompt(
    user_query: str,
    intent: str,
    queried_params: dict,
    tool_results: list[dict],
) -> str:
    """构建发给 LLM 的报告生成 prompt，使用压缩后的工具数据以降低 TTFT。"""
    compressed = _compress_tool_results(tool_results)
    tool_data_json = json.dumps(compressed, ensure_ascii=False, indent=2, default=str)

    return f"""## User Query
{user_query}

## Classified Intent
{intent}

## Query Parameters Used
{json.dumps(queried_params, ensure_ascii=False, indent=2)}

## Tool Results (summary)
{tool_data_json}

## Instructions
Generate a professional Markdown report based ONLY on the data above.
Follow ALL anti-hallucination rules strictly.
REMEMBER: include the MANDATORY "## 数据校验" section at the end.

## Report
"""


# ============================================================================
# 后置校验
# ============================================================================

def _validate_report(report_md: str, tool_results: list[dict],
                     queried_params: dict) -> list[str]:
    """校验报告中出现的年份是否在 tool_results/queried_params 范围内。

    这是轻量级的正则检查——不依赖 LLM，速度极快。
    返回警告列表，空列表表示校验通过。
    """
    warnings: list[str] = []

    # 收集工具结果中实际出现的所有年份
    valid_years: set[int] = set()

    # 从 queried_params 中收集
    for key in ("year", "start_date", "end_date"):
        val = queried_params.get(key)
        if isinstance(val, int):
            valid_years.add(val)
        elif isinstance(val, str) and len(val) >= 4:
            try:
                valid_years.add(int(val[:4]))
            except ValueError:
                pass

    # 从 tool_results 的 data.queried_params 中收集
    for r in tool_results:
        data = r.get("data", {})
        if isinstance(data, dict):
            qp = data.get("queried_params", {})
            for key in ("year", "start_date", "end_date"):
                val = qp.get(key)
                if isinstance(val, int):
                    valid_years.add(val)
                elif isinstance(val, str) and len(val) >= 4:
                    try:
                        valid_years.add(int(val[:4]))
                    except ValueError:
                        pass
            # 也从 data.data 的 date 字段收集
            for record_list_key in ("data", "fault_log", "monthly_breakdown"):
                records = data.get(record_list_key, [])
                if isinstance(records, list):
                    for rec in records:
                        if isinstance(rec, dict):
                            for date_key in ("date", "timestamp"):
                                date_str = rec.get(date_key, "")
                                if isinstance(date_str, str) and len(date_str) >= 4:
                                    try:
                                        valid_years.add(int(date_str[:4]))
                                    except ValueError:
                                        pass

    if not valid_years:
        return warnings  # 没有年份数据，跳过

    # 在报告中查找所有 4 位数字（可能是年份）
    year_pattern = re.findall(r'\b(20\d{2})\b', report_md)
    years_in_report = {int(y) for y in year_pattern}

    unknown = years_in_report - valid_years
    if unknown:
        warnings.append(
            f"⚠️ 报告中出现未在数据源中出现的年份: {sorted(unknown)}。"
            f"数据源年份范围: {sorted(valid_years)}。"
        )

    return warnings


# ============================================================================
# 主节点
# ============================================================================

async def report_node(state: AgentState) -> AgentState:
    """生成 Markdown 报告 + 校验 + 持久化。

    数据流:
        入: state["messages"], state["intent"], state["tool_results"], state["query_params"]
        出: state["report_md"]
    """
    settings = get_settings()
    intent = state.get("intent", "general_chat")
    tool_results = state.get("tool_results", [])
    session_id = state.get("session_id", "unknown")
    query_params = state.get("query_params", {})

    # 提取用户问题
    messages = state.get("messages", [])
    user_query = "No query"
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            user_query = m.content if isinstance(m.content, str) else str(m.content)
            break

    logger.info(
        f"report_node: session={session_id}, intent={intent}, "
        f"tools={len(tool_results)}"
    )

    # --- 无工具结果（general_chat）--- 流式输出
    if not tool_results:
        try:
            full_response = ""
            buf = TokenBuffer()
            async for token in astream_with_retry([
                SystemMessage(content=PERSONA_PROMPT),
                HumanMessage(content=user_query),
            ]):
                token_str = token if isinstance(token, str) else ""
                if token_str:
                    full_response += token_str
                    await buf.feed(token_str)
            await buf.flush()  # 发射残留
            state["report_md"] = full_response
        except Exception as e:
            logger.exception(f"report_node: LLM call failed: {e}")
            await emit(StreamEvent(type="error", message=str(e)))
            state["report_md"] = f"抱歉，处理请求时出错: {e}"
        return state

    # --- 有工具结果：数据驱动报告 --- 流式输出
    prompt = _build_generation_prompt(user_query, intent, query_params, tool_results)
    prompt += "\n" + REPORT_CONSTRAINTS  # 追加详细约束，不放 system 里以控制 TTFT

    try:
        report_md = ""
        buf = TokenBuffer()
        async for token in astream_with_retry([
            SystemMessage(content=PERSONA_PROMPT),
            HumanMessage(content=prompt),
        ]):
            token_str = token if isinstance(token, str) else ""
            if token_str:
                report_md += token_str
                await buf.feed(token_str)
        await buf.flush()  # 发射残留

        # --- 后置校验 ---
        validation_warnings = _validate_report(report_md, tool_results, query_params)
        if validation_warnings:
            logger.warning(
                f"report_node: validation found {len(validation_warnings)} issues"
            )
            warn_block = "\n\n---\n## ⚠️ 自动校验警告\n" + "\n".join(
                f"- {w}" for w in validation_warnings
            )
            report_md += warn_block
            for line in warn_block.split("\n"):
                if line.strip():
                    await emit(StreamEvent(type="token", content=line + "\n"))

        state["report_md"] = report_md
        logger.info(f"report_node: generated {len(report_md)} chars")

        # 持久化到 Redis
        try:
            from app.storage import get_redis_client
            redis = get_redis_client()
            await redis.cache_report(session_id, report_md)
        except RuntimeError:
            logger.debug("report_node: Redis not connected, skipping cache")
        except Exception:
            logger.exception("report_node: failed to cache report to Redis")

    except Exception as e:
        logger.exception(f"report_node: LLM generation failed: {e}")
        await emit(StreamEvent(type="error", message=str(e)))
        state["report_md"] = (
            f"报告生成失败: {e}\n\n"
            f"意图: {intent}\n"
            f"工具结果数: {len(tool_results)}"
        )

    return state
