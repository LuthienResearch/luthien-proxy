"""No-op policy that performs no modifications.

This is a unified policy implementing both OpenAI and Anthropic interfaces,
passing through all requests, responses, and stream events unchanged.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from litellm.types.utils import ModelResponse

from luthien_proxy.llm.types.anthropic import AnthropicRequest, AnthropicResponse
from luthien_proxy.policy_core import (
    AnthropicPolicyInterface,
    AnthropicStreamEvent,
    BasePolicy,
    OpenAIPolicyInterface,
)
from luthien_proxy.policy_core.policy_context import PolicyContext

if TYPE_CHECKING:
    from luthien_proxy.llm.types import Request
    from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext

logger = logging.getLogger(__name__)


class NoOpPolicy(BasePolicy, OpenAIPolicyInterface, AnthropicPolicyInterface):
    """No-op policy that passes through all data unchanged.

    Implements both OpenAIPolicyInterface and AnthropicPolicyInterface,
    acting as the simplest possible policy for both platforms:
    - All request hooks return the request unchanged
    - All response hooks return the response unchanged
    - All streaming hooks pass through data unchanged
    """

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name for the policy."""
        return "NoOp"

    # -------------------------------------------------------------------------
    # OpenAI interface hooks
    # -------------------------------------------------------------------------

    async def on_openai_request(self, request: Request, context: PolicyContext) -> Request:
        """Pass through request unchanged."""
        return request

    async def on_openai_response(self, response: ModelResponse, context: PolicyContext) -> ModelResponse:
        """Pass through response unchanged."""
        return response

    async def on_chunk_received(self, ctx: StreamingPolicyContext) -> None:
        """Pass through chunk unchanged."""
        ctx.push_chunk(ctx.last_chunk_received)

    async def on_content_delta(self, ctx: StreamingPolicyContext) -> None:
        """No-op for content delta."""
        pass

    async def on_content_complete(self, ctx: StreamingPolicyContext) -> None:
        """No-op for content complete."""
        pass

    async def on_tool_call_delta(self, ctx: StreamingPolicyContext) -> None:
        """No-op for tool call delta."""
        pass

    async def on_tool_call_complete(self, ctx: StreamingPolicyContext) -> None:
        """No-op for tool call complete."""
        pass

    async def on_finish_reason(self, ctx: StreamingPolicyContext) -> None:
        """No-op for finish reason."""
        pass

    async def on_stream_complete(self, ctx: StreamingPolicyContext) -> None:
        """No-op for stream complete."""
        pass

    async def on_streaming_policy_complete(self, ctx: StreamingPolicyContext) -> None:
        """No-op for streaming policy complete."""
        pass

    # -------------------------------------------------------------------------
    # Anthropic interface hooks
    # -------------------------------------------------------------------------

    async def on_anthropic_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
        """Pass through Anthropic request unchanged."""
        return request

    async def on_anthropic_response(self, response: AnthropicResponse, context: PolicyContext) -> AnthropicResponse:
        """Pass through Anthropic response unchanged."""
        return response

    async def on_anthropic_stream_event(
        self, event: AnthropicStreamEvent, context: PolicyContext
    ) -> list[AnthropicStreamEvent]:
        """Pass through Anthropic stream event unchanged."""
        return [event]


__all__ = ["NoOpPolicy"]
