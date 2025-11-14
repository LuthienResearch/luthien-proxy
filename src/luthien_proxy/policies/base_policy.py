# ABOUTME: Base policy implementation with convenience methods

"""Base policy that performs no modifications."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from litellm.types.utils import ModelResponse

    from luthien_proxy.messages import Request
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.policy_core.streaming_policy_context import (
        StreamingPolicyContext,
    )

from luthien_proxy.policy_core.policy_protocol import PolicyProtocol

logger = logging.getLogger(__name__)


class BasePolicy(PolicyProtocol):
    """Base policy that provides default implementations for pass-through functionality and convenience methods."""

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name for the policy. Defaults to class name."""
        return self.__class__.__name__

    # Default implementations

    async def on_request(self, request: Request, context: PolicyContext) -> Request:
        """Pass through request without modification."""
        return request

    async def on_response(self, response: ModelResponse, context: PolicyContext) -> ModelResponse:
        """Pass through response without modification."""
        return response

    async def on_chunk_received(self, ctx: StreamingPolicyContext) -> None:
        """Called on every chunk. Implement this if you DON'T want to pass through every chunk unchanged."""
        ctx.push_chunk(ctx.last_chunk_received)

    async def on_content_delta(self, ctx: StreamingPolicyContext) -> None:
        """Default - do nothing. Implement this to act when content delta chunks arrive."""
        pass

    async def on_content_complete(self, ctx: StreamingPolicyContext) -> None:
        """Default - do nothing. Implement this to act when the last chunk in a content block arrives."""
        pass

    async def on_tool_call_delta(self, ctx: StreamingPolicyContext) -> None:
        """Default - do nothing. Implement this to act when tool call delta chunks arrive."""
        pass

    async def on_tool_call_complete(self, ctx: StreamingPolicyContext) -> None:
        """Default - do nothing. Implement this to act when the last chunk in a tool call block arrives."""
        pass

    async def on_finish_reason(self, ctx: StreamingPolicyContext) -> None:
        """Default - do nothing. Implement this to act when a chunk with finish_reason arrives."""
        pass

    async def on_stream_complete(self, ctx: StreamingPolicyContext) -> None:
        """Default - do nothing. Implement this to act when the stream completes."""
        pass

    def get_config(self) -> dict[str, Any]:
        """Return the configuration parameters for this policy instance.

        Returns:
            Dictionary of configuration parameters. Subclasses can override
            to return their specific configuration.
        """
        return {}
