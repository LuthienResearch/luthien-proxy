# ABOUTME: Debug policy that logs all streaming chunks for inspection

"""Debug policy for logging streaming chunk contents."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from litellm.types.utils import ModelResponse

if TYPE_CHECKING:
    from luthien_proxy.v2.messages import Request
    from luthien_proxy.v2.policy_core.policy_context import PolicyContext
    from luthien_proxy.v2.policy_core.streaming_policy_context import StreamingPolicyContext

from luthien_proxy.v2.policy_core.policy_protocol import PolicyProtocol

logger = logging.getLogger(__name__)


class DebugLoggingPolicy(PolicyProtocol):
    """Debug policy that logs ModelResponse chunks and passes them through."""

    async def on_request(self, request: Request, context: PolicyContext) -> Request:
        """Process request before sending to LLM."""
        return request

    async def on_chunk_received(self, ctx: StreamingPolicyContext) -> None:
        """Called on every chunk - log it and pass through."""
        chunk = ctx.original_streaming_response_state.raw_chunks[-1]

        # Log the full model_dump
        logger.info(f"[CHUNK] {json.dumps(chunk.model_dump(), indent=2)}")

        # Log hidden params if they exist
        if hasattr(chunk, "_hidden_params"):
            logger.info(f"[HIDDEN_PARAMS] {chunk._hidden_params}")

        # Pass through
        ctx.egress_queue.put_nowait(chunk)

    async def on_response(self, response, context) -> ModelResponse:
        """Process full response after receiving from LLM."""
        return response


__all__ = ["DebugLoggingPolicy"]
