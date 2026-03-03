"""Debug policy for logging requests, responses, and streaming events.

This policy logs data at INFO level for debugging purposes while passing
through all data unchanged. Supports both OpenAI (via LiteLLM) and Anthropic
native formats.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from anthropic.lib.streaming import MessageStreamEvent
from litellm.types.utils import ModelResponse

from luthien_proxy.policy_core import (
    AnthropicExecutionInterface,
    AnthropicPolicyEmission,
    AnthropicPolicyIOProtocol,
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
    """Serialize object to JSON, using str() as fallback for non-serializable types."""
    return json.dumps(obj, indent=2, default=str)


def _event_to_dict(event: MessageStreamEvent) -> dict[str, Any]:
    """Convert Anthropic stream event to dict for logging."""
    return event.model_dump()


class DebugLoggingPolicy(BasePolicy, OpenAIPolicyInterface, AnthropicExecutionInterface):
    """Debug policy that logs request/response/streaming data for both API formats.

    All hooks log relevant data at INFO level, record events to context for
    DB persistence, and pass data through unchanged.
    """

    @property
    def short_policy_name(self) -> str:
        """Return 'DebugLogging'."""
        return "DebugLogging"

    # -- OpenAI Interface ------------------------------------------------------

    async def on_openai_request(self, request: "Request", context: "PolicyContext") -> "Request":
        """Log raw HTTP request data."""
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
        """Pass through unchanged."""
        return response

    async def on_chunk_received(self, ctx: "StreamingPolicyContext") -> None:
        """Log each chunk and pass through."""
        chunk = ctx.original_streaming_response_state.raw_chunks[-1]

        logger.info(f"[CHUNK] {json.dumps(chunk.model_dump(), indent=2)}")

        if hasattr(chunk, "_hidden_params"):
            logger.info(f"[HIDDEN_PARAMS] {chunk._hidden_params}")

        ctx.egress_queue.put_nowait(chunk)

    async def on_content_delta(self, ctx: "StreamingPolicyContext") -> None:
        """No-op."""
        pass

    async def on_content_complete(self, ctx: "StreamingPolicyContext") -> None:
        """No-op."""
        pass

    async def on_tool_call_delta(self, ctx: "StreamingPolicyContext") -> None:
        """No-op."""
        pass

    async def on_tool_call_complete(self, ctx: "StreamingPolicyContext") -> None:
        """No-op."""
        pass

    async def on_finish_reason(self, ctx: "StreamingPolicyContext") -> None:
        """No-op."""
        pass

    async def on_stream_complete(self, ctx: "StreamingPolicyContext") -> None:
        """No-op."""
        pass

    async def on_streaming_policy_complete(self, ctx: "StreamingPolicyContext") -> None:
        """No-op."""
        pass

    # -- Anthropic execution interface -----------------------------------------

    def run_anthropic(
        self, io: AnthropicPolicyIOProtocol, context: "PolicyContext"
    ) -> AsyncIterator[AnthropicPolicyEmission]:
        """Log Anthropic request/response/stream events while passing through."""

        async def _run() -> AsyncIterator[AnthropicPolicyEmission]:
            final_request = await self.on_anthropic_request(io.request, context)
            io.set_request(final_request)

            if final_request.get("stream", False):
                async for event in io.stream(final_request):
                    emitted_events = await self.on_anthropic_stream_event(event, context)
                    for emitted_event in emitted_events:
                        yield emitted_event
                return

            response = await io.complete(final_request)
            yield await self.on_anthropic_response(response, context)

        return _run()

    # -- Anthropic helpers -----------------------------------------------------

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
