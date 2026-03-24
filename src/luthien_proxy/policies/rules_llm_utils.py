"""Shared LLM call helper for rules-based policies."""

from __future__ import annotations

from typing import Any, cast

from litellm import acompletion
from litellm.types.utils import Choices, Message, ModelResponse


async def call_llm(
    messages: list[dict[str, str]],
    *,
    model: str,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    api_base: str | None = None,
    api_key: str | None = None,
) -> str:
    """Call an LLM and return the text content of its response."""
    kwargs: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if api_base:
        kwargs["api_base"] = api_base
    if api_key:
        kwargs["api_key"] = api_key

    response = await acompletion(**kwargs)
    response = cast(ModelResponse, response)
    first_choice: Choices = response.choices[0]  # type: ignore[assignment]
    message: Message = first_choice.message  # type: ignore[assignment]
    content = message.content or ""
    return content if isinstance(content, str) else str(content)


__all__ = ["call_llm"]
