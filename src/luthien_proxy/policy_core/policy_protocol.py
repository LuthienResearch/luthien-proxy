# ABOUTME: Base Policy interface with full streaming control
# ABOUTME: Provides hooks for request, chunk events, content/tool call completion, and non-streaming responses

"""Module docstring."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from litellm.types.utils import ModelResponse

    from luthien_proxy.messages import Request
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.policy_core.streaming_policy_context import (
        StreamingPolicyContext,
    )


@runtime_checkable
class PolicyProtocol(Protocol):
    """Protocol defining the policy interface. Not every method needs to be implemented."""

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name for the policy (e.g., 'NoOp', 'AllCaps', 'ToolJudge')."""
        ...

    async def on_request(self, request: Request, context: PolicyContext) -> Request:
        """Process request before sending to LLM."""
        ...

    async def on_response(self, response: ModelResponse, context: PolicyContext) -> ModelResponse:
        """Process non-streaming response after receiving from LLM."""
        ...

    async def on_chunk_received(self, ctx: StreamingPolicyContext) -> None:
        """Called on every chunk."""
        ...

    async def on_content_delta(self, ctx: StreamingPolicyContext) -> None:
        """Called when content delta received."""
        ...

    async def on_content_complete(self, ctx: StreamingPolicyContext) -> None:
        """Called when content block completes."""
        ...

    async def on_tool_call_delta(self, ctx: StreamingPolicyContext) -> None:
        """Called when tool call delta received."""
        ...

    async def on_tool_call_complete(self, ctx: StreamingPolicyContext) -> None:
        """Called when tool call block completes."""
        ...

    async def on_finish_reason(self, ctx: StreamingPolicyContext) -> None:
        """Called when finish_reason received."""
        ...

    async def on_stream_complete(self, ctx: StreamingPolicyContext) -> None:
        """Called when stream completes."""
        ...

    async def on_streaming_policy_complete(self, ctx: StreamingPolicyContext) -> None:
        """Called after all streaming policy processing completes for this request.

        This hook is guaranteed to run even if errors occurred during policy processing.
        Common uses include cleaning up buffers, caches, or other per-request state.

        IMPORTANT: This method should NOT emit any chunks or modify responses.
        It is called after all response processing is complete.

        Args:
            ctx: The streaming policy context for this request.
        """
        ...
