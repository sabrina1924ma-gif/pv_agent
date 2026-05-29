"""
DeepSeek API LLM 提供者。

使用 langchain-openai 的 ChatOpenAI 连接到 DeepSeek 的 OpenAI 兼容接口
(https://api.deepseek.com)。支持两种运行模式：
    - 生成模式 (temperature=0.3)：推理和报告生成。
    - 分类模式 (temperature=0)：快速、确定性的意图路由。

模型选项（截至 2026 年 5 月）：
    - deepseek-v4-pro  (1.6T/49B MoE)：高级推理，500 并发
    - deepseek-v4-flash (284B/13B MoE)：高性价比，2500 并发
    两者均支持 1M 上下文窗口。

API 文档：https://api-docs.deepseek.com
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any

from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage

from app.config import get_settings
from loguru import logger
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)


# ============================================================================
# 系统提示词
# ============================================================================

SYSTEM_PROMPT_CLASSIFICATION = """\
You are an intent classification system for a power/energy management platform.

Analyze the user's query and classify it into exactly ONE of these intents:
- device_status: The user wants to check real-time operational status of a device or station.
- power_curve: The user wants to see historical power output data or curves.
- fault_detection: The user wants to analyze faults, alarms, or anomalies.
- life_assessment: The user wants to assess remaining life or health of equipment.
- report: The user wants a comprehensive report generated from data.
- multi_station_summary: The user wants to compare metrics across multiple stations.
- general_chat: Casual conversation or questions not requiring data tools.

Respond with ONLY the intent label, nothing else. No explanations, no punctuation.
"""

SYSTEM_PROMPT_GENERATION = """\
You are a professional power systems analyst assistant for a photovoltaic/energy
management platform. Your role is to help users understand their equipment data,
diagnose issues, and generate actionable insights.

Guidelines:
- Use precise technical language appropriate for power engineers.
- When presenting data, use structured formats (tables, lists).
- Highlight anomalies, trends, and areas requiring attention.
- When generating reports, follow Markdown formatting conventions.
- Be concise but thorough. Prioritize actionable information.
- If data is insufficient for a conclusion, state what additional data is needed.
- Always maintain a professional and helpful tone.

You have access to tools that can query:
- Real-time device status and metrics
- Historical power output curves
- Fault logs and alarm records
- Equipment life assessment models
- Multi-station comparative analytics
"""


# ============================================================================
# DeepSeekProvider
# ============================================================================

class DeepSeekProvider:
    """
    通过 OpenAI 兼容接口管理 DeepSeek API 连接。

    两个缓存实例：
        - Primary（生成）：中等温度，用于报告/推理。
        - Light（分类）：temperature=0，用于确定性的意图路由。

    两者都通过相同的 DeepSeek API，但使用不同的运行时参数。
    底层的 ChatOpenAI 客户端是无状态的——模型实例可以安全地在并发请求中重用。
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._primary_model: ChatOpenAI | None = None
        self._light_model: ChatOpenAI | None = None

    @property
    def primary_model(self) -> ChatOpenAI:
        """
        用于推理、生成和报告编写的主要模型。

        配置为中等温度（~0.3），以获得连贯但非确定性的输出。
        使用设置中的默认模型。
        """
        if self._primary_model is None:
            self._primary_model = self._build_model(
                model_name=self._settings.deepseek_model,
                temperature=self._settings.llm_temperature,
            )
        return self._primary_model

    @property
    def light_model(self) -> ChatOpenAI:
        """
        轻量级分类器：相同模型，temperature=0 以确保确定性。

        DeepSeek V4 没有独立的"轻量"和"重量"模型——我们
        复用相同的模型名称，但将温度固定为 0 用于分类。
        这样可以跨调用获得可复现的意图标签。
        """
        if self._light_model is None:
            self._light_model = self._build_model(
                model_name=self._settings.deepseek_light_model,
                temperature=0.0,
            )
        return self._light_model

    def _build_model(
        self,
        model_name: str,
        temperature: float,
    ) -> ChatOpenAI:
        """
        构造指向 DeepSeek API 的 ChatOpenAI 实例。

        DeepSeek 的 API 与 OpenAI 兼容：相同的 /v1/chat/completions 端点，
        相同的 JSON 请求/响应格式。我们只需更换 base_url 并使用
        DeepSeek 模型名称替代 gpt-4。

        Args:
            model_name: 例如 "deepseek-v4-flash" 或 "deepseek-v4-pro"。
            temperature: 0.0（确定性）到 1.0（创造性）。

        Returns:
            配置好的 ChatOpenAI 实例，可用于 ainvoke() / astream()。
        """
        logger.info(
            f"Initializing DeepSeek model: {model_name} "
            f"(temp={temperature}, base_url={self._settings.deepseek_base_url})"
        )

        return ChatOpenAI(
            model=model_name,
            base_url=self._settings.deepseek_base_url,
            api_key=self._settings.deepseek_api_key,
            temperature=temperature,
            max_tokens=self._settings.llm_max_tokens,
            timeout=self._settings.llm_timeout_seconds,
            streaming=True,  # 启用真正的 SSE 流式传输，否则 astream() 退化为一次性返回
        )

    def get_model(
        self,
        model_name: str | None = None,
        temperature: float | None = None,
    ) -> BaseChatModel:
        """
        获取模型实例，可选择覆盖默认参数。

        Args:
            model_name: 覆盖模型名称。如果为 None，则使用 settings.deepseek_model。
            temperature: 覆盖温度。如果为 None，则使用 settings.llm_temperature。

        Returns:
            一个 ChatOpenAI 实例（BaseChatModel 兼容）。

        用法:
            llm = get_llm()                          # 默认主要模型
            llm = get_llm(temperature=0)             # 确定性生成
            llm = get_llm(model_name="deepseek-v4-pro")  # 高级推理
        """
        temp = temperature if temperature is not None else self._settings.llm_temperature
        model = model_name or self._settings.deepseek_model

        # 如果参数匹配默认值，使用缓存的实例
        if (
            model == self._settings.deepseek_model
            and temp == self._settings.llm_temperature
        ):
            return self.primary_model
        if (
            model == self._settings.deepseek_light_model
            and temp == 0.0
        ):
            return self.light_model

        # 对于非标准参数，临时构建
        return self._build_model(model_name=model, temperature=temp)


# ------------------------------------------------------------------
# 单例访问器
# ------------------------------------------------------------------

_llm_provider: DeepSeekProvider | None = None


@lru_cache
def get_llm(
    model_name: str | None = None,
    temperature: float | None = None,
) -> BaseChatModel:
    """
    返回指向 DeepSeek 的缓存 LangChain 兼容 LLM 实例。

    对于默认参数，返回单例以重用连接。
    对于覆盖的参数，创建新实例。

    Args:
        model_name: "deepseek-v4-flash"（默认）或 "deepseek-v4-pro"。
        temperature: 0.0（确定性）到 1.0（创造性）。默认 0.3。

    Returns:
        可用于 ainvoke() 或 astream() 的 BaseChatModel。

    示例:
        llm = get_llm()                          # flash, temp=0.3
        llm = get_llm(temperature=0)             # flash, 确定性
        llm = get_llm(model_name="deepseek-v4-pro")  # 高级推理
    """
    global _llm_provider
    if _llm_provider is None:
        _llm_provider = DeepSeekProvider()
    return _llm_provider.get_model(model_name=model_name, temperature=temperature)


# ============================================================================
# 重试包装器——防御 ThinkingBlock-only 响应和临时 API 故障。
#
# DeepSeek V4 有时在推理引擎卡住时只返回 ThinkingBlock（无 TextBlock）。
# tenacity 重试装饰器通过最多重试 3 次并采用指数退避来处理此问题。
# ============================================================================

class EmptyResponseError(Exception):
    """当 LLM 返回无可用的文本内容时抛出。"""


def _extract_content(msg: AIMessage) -> str:
    """从 AIMessage 中提取文本内容。如果为空则抛出 EmptyResponseError。"""
    content = msg.content
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        text = "".join(p for p in content if isinstance(p, str))
        if text.strip():
            return text
    raise EmptyResponseError("LLM returned empty or ThinkingBlock-only response")


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((EmptyResponseError, ConnectionError, TimeoutError)),
    reraise=True,
)
async def ainvoke_with_retry(messages: list[BaseMessage], temperature: float | None = None) -> str:
    """
    调用 LLM，在空响应或临时错误时重试。

    Args:
        messages: LangChain 消息列表（SystemMessage、HumanMessage 等）。
        temperature: 覆盖温度（None = 使用默认 0.3）。

    Returns:
        LLM 响应的文本内容。

    Raises:
        EmptyResponseError: 重试 3 次后，LLM 仍未返回文本。
        Exception: 未被重试过滤器捕获的其他错误。
    """
    llm = get_llm(temperature=temperature)
    response = await llm.ainvoke(messages)
    return _extract_content(response)


async def astream_with_retry(
    messages: list[BaseMessage],
    temperature: float | None = None,
) -> AsyncIterator[str]:
    """
    流式输出 LLM 结果，在空响应时重试。

    与 ainvoke_with_retry 不同，流式输出逐个 token 地生成。
    如果所有重试都失败，则抛出 EmptyResponseError。

    Args:
        messages: LangChain 消息列表。
        temperature: 覆盖温度（None = 使用默认 0.3）。

    Yields:
        LLM 的文本 token，每个 chunk 一个。

    Raises:
        EmptyResponseError: 重试 3 次后，仍未生成任何 token。
    """
    last_exception: Exception | None = None

    for attempt in range(1, 4):
        try:
            llm = get_llm(temperature=temperature)
            had_content = False

            async for chunk in llm.astream(messages):
                token = chunk.content if hasattr(chunk, "content") and chunk.content else ""
                if isinstance(token, str) and token:
                    had_content = True
                    yield token
                elif isinstance(token, list):
                    for part in token:
                        if isinstance(part, str) and part:
                            had_content = True
                            yield part

            if not had_content:
                raise EmptyResponseError(f"Streaming attempt {attempt}: LLM returned no content")

            return  # Success — all tokens yielded

        except EmptyResponseError:
            last_exception = EmptyResponseError(
                f"LLM returned empty/ThinkingBlock-only response after {attempt} attempt(s)"
            )
            logger.warning(f"LLM retry {attempt}/3: empty response, backing off...")
        except (ConnectionError, TimeoutError) as e:
            last_exception = e
            logger.warning(f"LLM retry {attempt}/3: {type(e).__name__}: {e}")

    raise last_exception or EmptyResponseError("LLM failed after 3 retries")
