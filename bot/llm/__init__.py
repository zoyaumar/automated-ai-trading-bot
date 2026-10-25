"""LLM provider factory."""

from .base import LLMInterface, LLMResult
from .openai_compat import OpenAICompatLLM
from .anthropic import AnthropicLLM


def get_llm(provider: str, **kwargs: object) -> LLMInterface:
    if provider == "openai_compat":
        return OpenAICompatLLM(**kwargs)
    if provider == "anthropic":
        return AnthropicLLM(**kwargs)
    raise ValueError(f"unknown LLM provider: {provider}")