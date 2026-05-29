"""
DeepSeek API LLM provider.

Uses langchain-openai's ChatOpenAI pointed at DeepSeek's OpenAI-compatible
endpoint (https://api.deepseek.com). Supports two operational modes:
    - Generation mode (temperature=0.3): reasoning and report generation.
    - Classification mode (temperature=0): fast, deterministic intent routing.

Model options (as of 2026-05):
    - deepseek-v4-pro  (1.6T/49B MoE): premium reasoning, 500 concurrent
    - deepseek-v4-flash (284B/13B MoE): high value, 2500 concurrent
    Both support 1M context window.

API doc: https://api-docs.deepseek.com
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
# System Prompts
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
    Manages DeepSeek API connections via OpenAI-compatible endpoint.

    Two cached instances:
        - Primary (generation): moderate temperature for report/reasoning.
        - Light (classification): temperature=0 for deterministic intent routing.

    Both go through the same DeepSeek API but with different runtime parameters.
    The underlying ChatOpenAI client is stateless — model instances are safe to
    reuse across concurrent requests.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._primary_model: ChatOpenAI | None = None
        self._light_model: ChatOpenAI | None = None

    @property
    def primary_model(self) -> ChatOpenAI:
        """
        Primary model for reasoning, generation, and report writing.

        Configured with moderate temperature (~0.3) for coherent but
        non-deterministic output. Uses the DEFAULT model from settings.
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
        Lightweight classifier: same model, temperature=0 for determinism.

        DeepSeek V4 doesn't have separate "light" and "heavy" models — we
        reuse the same model name but pin temperature to 0 for classification.
        This gives reproducible intent labels across calls.
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
        Construct a ChatOpenAI instance pointed at DeepSeek's API.

        DeepSeek's API is OpenAI-compatible: same /v1/chat/completions endpoint,
        same JSON request/response format. We just swap the base_url and use
        a DeepSeek model name instead of gpt-4.

        Args:
            model_name: e.g. "deepseek-v4-flash" or "deepseek-v4-pro".
            temperature: 0.0 (deterministic) to 1.0 (creative).

        Returns:
            Configured ChatOpenAI instance ready for ainvoke() / astream().
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
        Get a model instance, optionally overriding defaults.

        Args:
            model_name: Override the model. If None, uses settings.deepseek_model.
            temperature: Override temperature. If None, uses settings.llm_temperature.

        Returns:
            A ChatOpenAI instance (BaseChatModel-compatible).

        Usage:
            llm = get_llm()                          # default primary model
            llm = get_llm(temperature=0)             # deterministic generation
            llm = get_llm(model_name="deepseek-v4-pro")  # premium reasoning
        """
        temp = temperature if temperature is not None else self._settings.llm_temperature
        model = model_name or self._settings.deepseek_model

        # Use cached instance if parameters match defaults
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

        # Build ad-hoc for non-standard parameters
        return self._build_model(model_name=model, temperature=temp)


# ------------------------------------------------------------------
# Singleton accessor
# ------------------------------------------------------------------

_llm_provider: DeepSeekProvider | None = None


@lru_cache
def get_llm(
    model_name: str | None = None,
    temperature: float | None = None,
) -> BaseChatModel:
    """
    Return a cached LangChain-compatible LLM instance pointed at DeepSeek.

    For default parameters, returns a singleton for connection reuse.
    For overridden parameters, creates a new instance.

    Args:
        model_name: "deepseek-v4-flash" (default) or "deepseek-v4-pro".
        temperature: 0.0 (deterministic) to 1.0 (creative). Default 0.3.

    Returns:
        BaseChatModel ready for ainvoke() or astream().

    Examples:
        llm = get_llm()                          # flash, temp=0.3
        llm = get_llm(temperature=0)             # flash, deterministic
        llm = get_llm(model_name="deepseek-v4-pro")  # premium reasoning
    """
    global _llm_provider
    if _llm_provider is None:
        _llm_provider = DeepSeekProvider()
    return _llm_provider.get_model(model_name=model_name, temperature=temperature)


# ============================================================================
# Retry wrappers — defense against ThinkingBlock-only responses and transient
# API failures.
#
# DeepSeek V4 sometimes returns only a ThinkingBlock (no TextBlock) when
# the reasoning engine gets stuck. The tenacity retry decorator handles this
# by retrying up to 3 times with exponential backoff.
# ============================================================================

class EmptyResponseError(Exception):
    """Raised when the LLM returns no usable text content."""


def _extract_content(msg: AIMessage) -> str:
    """Extract text content from an AIMessage. Raises EmptyResponseError if empty."""
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
    Call the LLM with retry on empty responses or transient errors.

    Args:
        messages: List of LangChain messages (SystemMessage, HumanMessage, etc.).
        temperature: Override temperature (None = use default 0.3).

    Returns:
        The text content of the LLM response.

    Raises:
        EmptyResponseError: After 3 retries, if LLM still returns no text.
        Exception: Other errors that aren't caught by the retry filter.
    """
    llm = get_llm(temperature=temperature)
    response = await llm.ainvoke(messages)
    return _extract_content(response)


async def astream_with_retry(
    messages: list[BaseMessage],
    temperature: float | None = None,
) -> AsyncIterator[str]:
    """
    Stream LLM output with retry on empty responses.

    Unlike ainvoke_with_retry, streaming output is yielded token-by-token.
    If all retries fail, raises EmptyResponseError.

    Args:
        messages: List of LangChain messages.
        temperature: Override temperature (None = use default 0.3).

    Yields:
        Text tokens from the LLM, one per chunk.

    Raises:
        EmptyResponseError: After 3 retries, if no tokens were produced.
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
