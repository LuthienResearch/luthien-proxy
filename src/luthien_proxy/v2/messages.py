# ABOUTME: Message type definitions for policy processing
# ABOUTME: Explicit types for Request, FullResponse, and StreamingResponse

"""Message types for policy processing.

These types make explicit what policies are operating on:
- Request: The request sent to the LLM (OpenAI format)
- FullResponse: A complete response from the LLM (non-streaming)
- StreamingResponse: A single chunk in a streaming response
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    ModelResponse = Any  # LiteLLM's ModelResponse has incomplete type annotations


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


class FullResponse(BaseModel):
    """A complete (non-streaming) response from an LLM.

    This wraps the actual ModelResponse from LiteLLM. Policies can:
    - Inspect the response content
    - Filter or transform the content
    - Add metadata
    - Reject the response (by raising an exception)
    """

    response: Any = Field(description="The actual ModelResponse from LiteLLM")

    @classmethod
    def from_model_response(cls, response: ModelResponse) -> FullResponse:
        """Create from a LiteLLM ModelResponse."""
        return cls(response=response)

    def to_model_response(self) -> Any:
        """Extract the underlying ModelResponse."""
        return self.response

    model_config = {"arbitrary_types_allowed": True}


class StreamingResponse(BaseModel):
    """A single chunk in a streaming response.

    This wraps a single streaming chunk from LiteLLM. Policies can:
    - Inspect chunk content
    - Filter chunks (don't emit)
    - Transform chunks
    - Buffer chunks and emit in batches
    - Inject additional chunks
    - Abort the stream

    Important: There's no 1:1 mapping between input and output chunks.
    A policy might:
    - Consume 5 chunks before emitting 1 (buffering)
    - Emit 3 chunks for each input chunk (splitting)
    - Not emit a chunk at all (filtering)
    """

    chunk: Any = Field(description="The actual streaming chunk from LiteLLM")

    @classmethod
    def from_model_response(cls, chunk: ModelResponse) -> StreamingResponse:
        """Create from a LiteLLM streaming chunk."""
        return cls(chunk=chunk)

    def to_model_response(self) -> Any:
        """Extract the underlying chunk."""
        return self.chunk

    model_config = {"arbitrary_types_allowed": True}


__all__ = [
    "Request",
    "FullResponse",
    "StreamingResponse",
]
