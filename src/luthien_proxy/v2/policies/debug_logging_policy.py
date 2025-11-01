# ABOUTME: Debug policy that logs all streaming chunks for inspection

"""Debug policy for logging streaming chunk contents."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from litellm.types.utils import ModelResponse

if TYPE_CHECKING:
    from luthien_proxy.v2.messages import Request
    from luthien_proxy.v2.policies.policy import PolicyContext
    from luthien_proxy.v2.streaming.streaming_response_context import StreamingResponseContext

from luthien_proxy.v2.policies.policy import Policy

logger = logging.getLogger(__name__)


class DebugLoggingPolicy(Policy):
    """Debug policy that logs ModelResponse chunks and passes them through."""

    async def on_request(self, request: Request, context: PolicyContext) -> Request:
        """Process request before sending to LLM."""
        return request

    async def on_chunk_received(self, ctx: StreamingResponseContext) -> None:
        """Called on every chunk - log it and pass through."""
        chunk = ctx.ingress_state.raw_chunks[-1]

        # Log the full model_dump
        logger.info(f"[CHUNK] {json.dumps(chunk.model_dump(), indent=2)}")

        # Log hidden params if they exist
        if hasattr(chunk, "_hidden_params"):
            logger.info(f"[HIDDEN_PARAMS] {chunk._hidden_params}")

        # Pass through
        ctx.egress_queue.put_nowait(chunk)

    async def process_full_response(self, response, context) -> ModelResponse:
        """Process full response after receiving from LLM."""
        return response


__all__ = ["DebugLoggingPolicy"]
