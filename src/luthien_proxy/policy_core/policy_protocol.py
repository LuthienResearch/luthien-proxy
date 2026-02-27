"""Protocol defining the policy interface for request/response processing.

This module defines PolicyProtocol with hooks for:
- Streaming chunk events and content/tool call completion
- Stream lifecycle and cleanup

Used for type annotations in policy infrastructure. Concrete policies should
inherit from BasePolicy + OpenAIPolicyInterface/AnthropicExecutionInterface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
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

    def get_config(self) -> dict[str, Any]:
        """Get the configuration for this policy instance."""
        ...

    def freeze_configured_state(self) -> None:
        """Run post-configuration validation for policy instance shape."""
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
