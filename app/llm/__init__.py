"""LLM layer: DeepSeek API via OpenAI-compatible endpoint (LangChain integration)."""

from app.llm.provider import DeepSeekProvider, get_llm

__all__ = ["DeepSeekProvider", "get_llm"]
