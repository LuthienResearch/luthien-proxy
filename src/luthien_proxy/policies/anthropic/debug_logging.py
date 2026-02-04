# ABOUTME: Debug logging policy implementing AnthropicPolicyProtocol for logging requests/responses/events
"""Debug logging policy for Anthropic-native requests.

This policy logs request, response, and stream event data for debugging purposes.
It passes through all data unchanged while providing visibility into the
Anthropic message flow.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from luthien_proxy.policy_core.anthropic_protocol import (
    AnthropicStreamEvent,
)

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicRequest,
        AnthropicResponse,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)


def _safe_json_dump(obj: Any) -> str:
    """Safely serialize object to JSON, handling non-serializable types."""
    try:
        return json.dumps(obj, indent=2, default=str)
    except (TypeError, ValueError) as e:
        return f"<serialization error: {e}>"


def _event_to_dict(event: AnthropicStreamEvent) -> dict[str, Any]:
    """Convert stream event to dict for logging."""
    if hasattr(event, "model_dump"):
        return event.model_dump()
    return {"type": getattr(event, "type", "unknown"), "repr": repr(event)}


class AnthropicDebugLoggingPolicy:
    """Debug policy that logs Anthropic request/response/event data.

    Implements AnthropicPolicyProtocol:
    - on_request: Logs request data and passes through unchanged
    - on_response: Logs response data and passes through unchanged
    - on_stream_event: Logs event data and passes through unchanged
    """

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name for the policy."""
        return "AnthropicDebugLogging"

    async def on_request(self, request: "AnthropicRequest", context: "PolicyContext") -> "AnthropicRequest":
        """Log request data before sending to Anthropic API."""
        logger.info(f"[ANTHROPIC_REQUEST] {_safe_json_dump(request)}")

        # Record as event for DB persistence
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

    async def on_response(self, response: "AnthropicResponse", context: "PolicyContext") -> "AnthropicResponse":
        """Log response data after receiving from Anthropic API."""
        logger.info(f"[ANTHROPIC_RESPONSE] {_safe_json_dump(response)}")

        # Record as event for DB persistence
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

    async def on_stream_event(
        self, event: AnthropicStreamEvent, context: "PolicyContext"
    ) -> AnthropicStreamEvent | None:
        """Log stream event data."""
        event_dict = _event_to_dict(event)
        logger.info(f"[ANTHROPIC_STREAM_EVENT] {_safe_json_dump(event_dict)}")

        # Record as event for DB persistence
        context.record_event(
            "debug.anthropic_stream_event",
            {
                "event_type": getattr(event, "type", "unknown"),
            },
        )

        return event


__all__ = ["AnthropicDebugLoggingPolicy"]
