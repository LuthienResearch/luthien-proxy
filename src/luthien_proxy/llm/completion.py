"""Provider-agnostic LLM completion interface.

Currently backed by the Anthropic SDK. The function signature is
designed so other providers can be added later without changing callers.
"""

from __future__ import annotations

from dataclasses import dataclass

import anthropic


@dataclass(frozen=True)
class CompletionResult:
    """Result from an LLM completion call."""

    text: str
    input_tokens: int
    output_tokens: int


async def completion(
    model: str,
    messages: list[dict[str, str]],
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    extra_headers: dict[str, str] | None = None,
) -> CompletionResult:
    """Call an LLM and return the text response.

    Messages use OpenAI-style format: [{"role": "system"/"user", "content": "..."}].
    System messages are extracted and passed via Anthropic's system parameter.

    Args:
        model: Anthropic model name (e.g. "claude-haiku-4-5").
        messages: List of message dicts with "role" and "content" keys.
        api_key: API key. If None, the SDK reads ANTHROPIC_API_KEY from env.
        base_url: Custom API base URL.
        temperature: Sampling temperature.
        max_tokens: Maximum output tokens.
        extra_headers: Additional HTTP headers (e.g. for beta features).

    Returns:
        CompletionResult with the text response and token usage.

    Raises:
        ValueError: If the response contains no text content.
    """
    # Separate system messages from conversation messages
    system_parts: list[str] = []
    conversation: list[dict[str, str]] = []
    for msg in messages:
        if msg["role"] == "system":
            system_parts.append(msg["content"])
        else:
            conversation.append(msg)

    # Build client kwargs
    client_kwargs: dict = {}
    if api_key is not None:
        client_kwargs["api_key"] = api_key
    if base_url is not None:
        client_kwargs["base_url"] = base_url

    client = anthropic.AsyncAnthropic(**client_kwargs)

    # Build request kwargs
    request_kwargs: dict = {
        "model": model,
        "messages": conversation,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if system_parts:
        request_kwargs["system"] = "\n\n".join(system_parts)
    if extra_headers:
        request_kwargs["extra_headers"] = extra_headers

    try:
        response = await client.messages.create(**request_kwargs)
    finally:
        await client.close()

    # Concatenate all text blocks
    text_parts = [block.text for block in response.content if hasattr(block, "text")]
    if not text_parts:
        raise ValueError("No text content in LLM response")

    return CompletionResult(
        text="\n".join(text_parts),
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )
