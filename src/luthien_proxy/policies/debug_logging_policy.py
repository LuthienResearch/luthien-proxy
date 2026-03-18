"""Debug policy for logging requests, responses, and streaming events.

This policy logs data at INFO level for debugging purposes while passing
through all data unchanged for native Anthropic formats.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from anthropic.lib.streaming import MessageStreamEvent

from luthien_proxy.policy_core import (
    AnthropicHookPolicy,
    BasePolicy,
)

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicRequest,
        AnthropicResponse,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)


def _safe_json_dump(obj: Any) -> str:
    """Serialize object to JSON, using str() as fallback for non-serializable types."""
    return json.dumps(obj, indent=2, default=str)


def _event_to_dict(event: MessageStreamEvent) -> dict[str, Any]:
    """Convert Anthropic stream event to dict for logging."""
    return event.model_dump()


class DebugLoggingPolicy(BasePolicy, AnthropicHookPolicy):
    """Debug policy that logs request/response/streaming data for Anthropic API.

    All hooks log relevant data at INFO level, record events to context for
    DB persistence, and pass data through unchanged.
    """

    @property
    def short_policy_name(self) -> str:
        """Return 'DebugLogging'."""
        return "DebugLogging"

    async def on_anthropic_request(self, request: "AnthropicRequest", context: "PolicyContext") -> "AnthropicRequest":
        """Log request summary."""
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
        """Log response summary."""
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
        self, event: MessageStreamEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        """Log stream event."""
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
