"""Abstract base class defining the OpenAI policy interface.

This module defines OpenAIPolicyInterface with hooks for:
- Non-streaming request and response processing
- Streaming chunk events and content/tool call completion
- Stream lifecycle and cleanup

Policies implementing this interface work with OpenAI-format types
(via LiteLLM), supporting models from various providers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from litellm.types.utils import ModelResponse

    from luthien_proxy.llm.types import Request
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.policy_core.streaming_policy_context import (
        StreamingPolicyContext,
    )


class OpenAIPolicyInterface(ABC):
    """Abstract base class for policies that work with OpenAI-format types.

    This interface defines hooks for processing OpenAI API requests and responses.
    Policies can implement any subset of these hooks; unimplemented hooks will
    use default passthrough behavior.

    For non-streaming:
    - on_openai_request: Transform request before sending to LLM
    - on_openai_response: Transform response before returning to client

    For streaming:
    - on_chunk_received: Called on every chunk
    - on_content_delta: Called when content delta received
    - on_content_complete: Called when content block completes
    - on_tool_call_delta: Called when tool call delta received
    - on_tool_call_complete: Called when tool call block completes
    - on_finish_reason: Called when finish_reason received
    - on_stream_complete: Called when stream completes
    - on_streaming_policy_complete: Called after all streaming processing
    """

    @abstractmethod
    async def on_openai_request(self, request: "Request", context: "PolicyContext") -> "Request":
        """Process request before sending to LLM.

        Args:
            request: The OpenAI-format request
            context: Request-scoped policy context (emitter, request state, etc.)

        Returns:
            Potentially modified request to send to the LLM
        """
        ...

    @abstractmethod
    async def on_openai_response(self, response: "ModelResponse", context: "PolicyContext") -> "ModelResponse":
        """Process non-streaming response after receiving from LLM.

        Args:
            response: The LiteLLM ModelResponse
            context: Request-scoped policy context (emitter, request state, etc.)

        Returns:
            Potentially modified response to return to client
        """
        ...

    async def on_chunk_received(self, ctx: "StreamingPolicyContext") -> None:
        """Called on every chunk received from the LLM.

        Default: forwards the chunk to the client unchanged.
        Override to filter, buffer, or transform individual chunks.
        """
        ctx.push_chunk(ctx.last_chunk_received)

    async def on_content_delta(self, ctx: "StreamingPolicyContext") -> None:
        """Called when a content delta is received. Default: no-op."""
        pass

    async def on_content_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Called when a content block completes. Default: no-op."""
        pass

    async def on_tool_call_delta(self, ctx: "StreamingPolicyContext") -> None:
        """Called when a tool call delta is received. Default: no-op."""
        pass

    async def on_tool_call_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Called when a tool call block completes. Default: no-op."""
        pass

    async def on_finish_reason(self, ctx: "StreamingPolicyContext") -> None:
        """Called when finish_reason is received. Default: no-op."""
        pass

    async def on_stream_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Called when stream completes. Default: no-op."""
        pass

    async def on_streaming_policy_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Called after all streaming processing completes. Default: no-op.

        Guaranteed to run even if errors occurred. Use for cleanup.
        IMPORTANT: Should NOT emit chunks or modify responses.
        """
        pass


__all__ = ["OpenAIPolicyInterface"]
