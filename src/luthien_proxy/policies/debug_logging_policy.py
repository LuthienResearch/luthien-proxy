"""Debug policy for logging streaming chunk contents."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from litellm.types.utils import ModelResponse

if TYPE_CHECKING:
    from luthien_proxy.messages import Request
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext

from luthien_proxy.policies.base_policy import BasePolicy

logger = logging.getLogger(__name__)


class DebugLoggingPolicy(BasePolicy):
    """Debug policy that logs ModelResponse chunks and passes them through."""

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name for the policy."""
        return "DebugLogging"

    async def on_request(self, request: Request, context: PolicyContext) -> Request:
        """Process request before sending to LLM - log raw HTTP request."""
        # Log the raw HTTP request (before format conversion)
        if context.raw_http_request is not None:
            raw = context.raw_http_request
            logger.info(f"[RAW_HTTP_REQUEST] method={raw.method} path={raw.path}")
            logger.info(f"[RAW_HTTP_REQUEST] headers={json.dumps(dict(raw.headers), indent=2)}")
            logger.info(f"[RAW_HTTP_REQUEST] body={json.dumps(raw.body, indent=2)}")

            # Also emit as an event for DB persistence
            context.record_event(
                "debug.raw_http_request",
                {
                    "method": raw.method,
                    "path": raw.path,
                    "headers": dict(raw.headers),
                    "body": raw.body,
                },
            )
        else:
            logger.warning("[RAW_HTTP_REQUEST] No raw HTTP request available in context")

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
