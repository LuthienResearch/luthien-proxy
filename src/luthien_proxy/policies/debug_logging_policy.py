# ABOUTME: Unified debug logging policy for both OpenAI and Anthropic API formats
"""Debug policy for logging requests, responses, and streaming events.

This policy logs data at INFO level for debugging purposes while passing
through all data unchanged. Supports both OpenAI (via LiteLLM) and Anthropic
native formats.

For OpenAI format:
- Logs raw HTTP request on on_openai_request
- Logs streaming chunks via on_chunk_received
- Passes through on_openai_response unchanged

For Anthropic format:
- Logs request summary on on_anthropic_request
- Logs response summary on on_anthropic_response
- Logs streaming events on on_anthropic_stream_event
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from litellm.types.utils import ModelResponse

from luthien_proxy.policy_core import (
    AnthropicPolicyInterface,
    AnthropicStreamEvent,
    BasePolicy,
    OpenAIPolicyInterface,
)

if TYPE_CHECKING:
    from luthien_proxy.llm.types import Request
    from luthien_proxy.llm.types.anthropic import (
        AnthropicRequest,
        AnthropicResponse,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext

logger = logging.getLogger(__name__)


def _safe_json_dump(obj: Any) -> str:
    """Safely serialize object to JSON, handling non-serializable types."""
    try:
        return json.dumps(obj, indent=2, default=str)
    except (TypeError, ValueError) as e:
        return f"<serialization error: {e}>"


def _event_to_dict(event: AnthropicStreamEvent) -> dict[str, Any]:
    """Convert Anthropic stream event to dict for logging."""
    if hasattr(event, "model_dump"):
        return event.model_dump()
    return {"type": getattr(event, "type", "unknown"), "repr": repr(event)}


class DebugLoggingPolicy(BasePolicy, OpenAIPolicyInterface, AnthropicPolicyInterface):
    """Debug policy that logs request/response/streaming data for both API formats.

    Implements both OpenAIPolicyInterface and AnthropicPolicyInterface:
    - All hooks log relevant data at INFO level
    - All data passes through unchanged
    - Events are recorded to context for DB persistence
    """

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name for the policy."""
        return "DebugLogging"

    # =========================================================================
    # OpenAI Interface Implementation
    # =========================================================================

    async def on_openai_request(self, request: "Request", context: "PolicyContext") -> "Request":
        """Process request before sending to LLM - log raw HTTP request."""
        if context.raw_http_request is not None:
            raw = context.raw_http_request
            logger.info(f"[RAW_HTTP_REQUEST] method={raw.method} path={raw.path}")
            logger.info(f"[RAW_HTTP_REQUEST] headers={json.dumps(dict(raw.headers), indent=2)}")
            logger.info(f"[RAW_HTTP_REQUEST] body={json.dumps(raw.body, indent=2)}")

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

    async def on_openai_response(self, response: ModelResponse, context: "PolicyContext") -> ModelResponse:
        """Process non-streaming response after receiving from LLM."""
        return response

    async def on_chunk_received(self, ctx: "StreamingPolicyContext") -> None:
        """Called on every chunk - log it and pass through."""
        chunk = ctx.original_streaming_response_state.raw_chunks[-1]

        logger.info(f"[CHUNK] {json.dumps(chunk.model_dump(), indent=2)}")

        if hasattr(chunk, "_hidden_params"):
            logger.info(f"[HIDDEN_PARAMS] {chunk._hidden_params}")

        ctx.egress_queue.put_nowait(chunk)

    async def on_content_delta(self, ctx: "StreamingPolicyContext") -> None:
        """Called when content delta received - no-op for debug logging."""
        pass

    async def on_content_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Called when content block completes - no-op for debug logging."""
        pass

    async def on_tool_call_delta(self, ctx: "StreamingPolicyContext") -> None:
        """Called when tool call delta received - no-op for debug logging."""
        pass

    async def on_tool_call_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Called when tool call block completes - no-op for debug logging."""
        pass

    async def on_finish_reason(self, ctx: "StreamingPolicyContext") -> None:
        """Called when finish_reason received - no-op for debug logging."""
        pass

    async def on_stream_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Called when stream completes - no-op for debug logging."""
        pass

    async def on_streaming_policy_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Called after all streaming policy processing - no-op for debug logging."""
        pass

    # =========================================================================
    # Anthropic Interface Implementation
    # =========================================================================

    async def on_anthropic_request(self, request: "AnthropicRequest", context: "PolicyContext") -> "AnthropicRequest":
        """Log request data before sending to Anthropic API."""
        logger.info(f"[ANTHROPIC_REQUEST] {_safe_json_dump(request)}")

        context.record_event(
            "debug.anthropic_request",
            {
                "model": request.get("model"),
                "message_count": len(request.get("messages", [])),
                "max_tokens": request.get("max_tokens"),
                "has_system": "system" in request,
                "has_tools": "tools" in request,
                "stream": request.get("stream", False),
            },
        )

        return request

    async def on_anthropic_response(
        self, response: "AnthropicResponse", context: "PolicyContext"
    ) -> "AnthropicResponse":
        """Log response data after receiving from Anthropic API."""
        logger.info(f"[ANTHROPIC_RESPONSE] {_safe_json_dump(response)}")

        context.record_event(
            "debug.anthropic_response",
            {
                "id": response.get("id"),
                "model": response.get("model"),
                "stop_reason": response.get("stop_reason"),
                "content_block_count": len(response.get("content", [])),
                "usage": response.get("usage"),
            },
        )

        return response

    async def on_anthropic_stream_event(
        self, event: AnthropicStreamEvent, context: "PolicyContext"
    ) -> list[AnthropicStreamEvent]:
        """Log stream event data."""
        event_dict = _event_to_dict(event)
        logger.info(f"[ANTHROPIC_STREAM_EVENT] {_safe_json_dump(event_dict)}")

        context.record_event(
            "debug.anthropic_stream_event",
            {
                "event_type": getattr(event, "type", "unknown"),
            },
        )

        return [event]


__all__ = ["DebugLoggingPolicy"]
