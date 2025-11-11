# ABOUTME: Do nothing policy implementation

"""No-op policy that performs no modifications."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from litellm.types.utils import ModelResponse

if TYPE_CHECKING:
    from luthien_proxy.messages import Request
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext

from luthien_proxy.policy_core.policy_protocol import PolicyProtocol

logger = logging.getLogger(__name__)


class NoOpPolicy(PolicyProtocol):
    """No-op policy that does nothing."""

    async def on_request(self, request: Request, context: PolicyContext) -> Request:
        """Process request before sending to LLM."""
        return request

    async def on_response(self, response: ModelResponse, context: PolicyContext) -> ModelResponse:
        """Process non-streaming response after receiving from LLM."""
        return response

    async def on_chunk_received(self, ctx: StreamingPolicyContext) -> None:
        """Called on every chunk."""
        ctx.egress_queue.put_nowait(ctx.original_streaming_response_state.raw_chunks[-1])
