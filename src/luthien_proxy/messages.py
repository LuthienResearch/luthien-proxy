# ABOUTME: Request message type definition for policy processing
# ABOUTME: Policies work directly with LiteLLM's ModelResponse for responses

"""Message types for policy processing.

Policies operate on:
- Request: The request sent to the LLM (OpenAI format) - our type
- ModelResponse: Complete LLM responses (non-streaming) - LiteLLM's type
- ModelResponse: Streaming chunks - also LiteLLM's type (same type, different usage)
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Request(BaseModel):
    """A request to an LLM (OpenAI format).

    This is what gets sent to the LLM provider. Policies can:
    - Validate the request
    - Transform parameters (e.g., clamp max_tokens)
    - Add metadata
    - Reject the request (by raising an exception)
    """

    model: str = Field(description="Model identifier (e.g., 'gpt-4', 'claude-3-5-sonnet-20241022')")
    messages: list[dict[str, Any]] = Field(description="Conversation messages in OpenAI format")
    max_tokens: int | None = Field(default=None, description="Maximum tokens to generate")
    temperature: float | None = Field(default=None, description="Sampling temperature")
    stream: bool = Field(default=False, description="Whether to stream the response")

    # Allow additional fields for provider-specific parameters
    model_config = {"extra": "allow"}


__all__ = ["Request"]
