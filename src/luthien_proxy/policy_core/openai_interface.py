# ABOUTME: Abstract base class defining the OpenAI policy interface

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
            context: Policy context with scratchpad, emitter, etc.

        Returns:
            Potentially modified request to send to the LLM
        """
        ...

    @abstractmethod
    async def on_openai_response(self, response: "ModelResponse", context: "PolicyContext") -> "ModelResponse":
        """Process non-streaming response after receiving from LLM.

        Args:
            response: The LiteLLM ModelResponse
            context: Policy context with scratchpad, emitter, etc.

        Returns:
            Potentially modified response to return to client
        """
        ...

    @abstractmethod
    async def on_chunk_received(self, ctx: "StreamingPolicyContext") -> None:
        """Called on every chunk received from the LLM.

        Args:
            ctx: Streaming policy context with current chunk and accumulated state
        """
        ...

    @abstractmethod
    async def on_content_delta(self, ctx: "StreamingPolicyContext") -> None:
        """Called when a content delta is received.

        Args:
            ctx: Streaming policy context with current chunk and accumulated state
        """
        ...

    @abstractmethod
    async def on_content_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Called when a content block completes.

        Args:
            ctx: Streaming policy context with completed content
        """
        ...

    @abstractmethod
    async def on_tool_call_delta(self, ctx: "StreamingPolicyContext") -> None:
        """Called when a tool call delta is received.

        Args:
            ctx: Streaming policy context with current chunk and accumulated state
        """
        ...

    @abstractmethod
    async def on_tool_call_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Called when a tool call block completes.

        Args:
            ctx: Streaming policy context with completed tool call
        """
        ...

    @abstractmethod
    async def on_finish_reason(self, ctx: "StreamingPolicyContext") -> None:
        """Called when finish_reason is received.

        Args:
            ctx: Streaming policy context with finish reason
        """
        ...

    @abstractmethod
    async def on_stream_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Called when stream completes.

        Args:
            ctx: Streaming policy context with completed stream state
        """
        ...

    @abstractmethod
    async def on_streaming_policy_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Called after all streaming policy processing completes for this request.

        This hook is guaranteed to run even if errors occurred during policy processing.
        Common uses include cleaning up buffers, caches, or other per-request state.

        IMPORTANT: This method should NOT emit any chunks or modify responses.
        It is called after all response processing is complete.

        Args:
            ctx: The streaming policy context for this request.
        """
        ...


__all__ = ["OpenAIPolicyInterface"]
