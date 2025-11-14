# ABOUTME: LuthienTransaction tracks complete request/response cycle with format transformations
# ABOUTME: Provides unified observability for all gateway endpoints. Note: We will probably delete this later.

# TODO: PROBABLY DELETE THIS FILE LATER

"""Luthien transaction tracking for request/response cycles."""

from __future__ import annotations

import logging
from typing import Any

from luthien_proxy.messages import Request
from luthien_proxy.observability.context import ObservabilityContext

logger = logging.getLogger(__name__)


class LuthienTransaction:
    """Tracks complete request/response cycle with all format transformations.

    This provides unified observability for both /v1/messages (Anthropic) and
    /v1/chat/completions (OpenAI) endpoints, tracking data at each stage:
    - Raw incoming request
    - Format conversions (if any)
    - Request sent to backend LLM
    - Response from backend LLM
    - Final response sent to client
    """

    def __init__(self, transaction_id: str, obs_ctx: ObservabilityContext):
        """Initialize transaction tracker.

        Args:
            transaction_id: Unique ID for this transaction
            obs_ctx: Observability context for emitting events
        """
        self.transaction_id = transaction_id
        self.obs_ctx = obs_ctx

    async def track_incoming_request(
        self,
        endpoint: str,
        body: dict[str, Any],
        client_format: str,
    ) -> None:
        """Track raw incoming request before any processing.

        Args:
            endpoint: API endpoint (/v1/messages or /v1/chat/completions)
            body: Raw request body
            client_format: Format of incoming request (anthropic or openai)
        """
        await self.obs_ctx.emit_event(
            event_type="luthien.request.incoming",
            data={
                "endpoint": endpoint,
                "format": client_format,
                "body": body,
            },
        )
        logger.info(
            f"[{self.transaction_id}] FORMAT_TRACKING: Incoming {client_format} request to {endpoint}, messages={len(body.get('messages', []))}"
        )

    async def track_format_conversion(
        self,
        conversion: str,
        input_format: str,
        output_format: str,
        result: dict[str, Any] | Request,
    ) -> None:
        """Track format conversion operation.

        Args:
            conversion: Description of conversion (e.g., "anthropic_to_openai")
            input_format: Input format name
            output_format: Output format name
            result: Converted data
        """
        result_dict = result.model_dump(exclude_none=True) if isinstance(result, Request) else result

        await self.obs_ctx.emit_event(
            event_type="luthien.request.format_conversion",
            data={
                "conversion": conversion,
                "input_format": input_format,
                "output_format": output_format,
                "result": result_dict,
            },
        )
        messages = result_dict.get("messages", [])
        logger.info(
            f"[{self.transaction_id}] FORMAT_TRACKING: Conversion {conversion}, result_messages={len(messages)}"
        )
        for i, msg in enumerate(messages):
            role = msg.get("role", "unknown")
            content_preview = str(msg.get("content", ""))[:100]
            logger.info(
                f"[{self.transaction_id}] FORMAT_TRACKING:   Message {i}: role={role}, content={content_preview}"
            )

    async def track_backend_request(self, request: Request) -> None:
        """Track final request sent to backend LLM.

        Args:
            request: Request object sent to backend
        """
        request_dict = request.model_dump(exclude_none=True)
        await self.obs_ctx.emit_event(
            event_type="luthien.backend.request",
            data={
                "request": request_dict,
            },
        )
        messages = request_dict.get("messages", [])
        logger.info(f"[{self.transaction_id}] FORMAT_TRACKING: Backend request, messages={len(messages)}")
        for i, msg in enumerate(messages):
            role = msg.get("role", "unknown")
            content_preview = str(msg.get("content", ""))[:100]
            logger.info(
                f"[{self.transaction_id}] FORMAT_TRACKING:   Message {i}: role={role}, content={content_preview}"
            )

    async def track_backend_response(
        self,
        response: Any,
        is_streaming: bool,
    ) -> None:
        """Track response from backend LLM.

        Args:
            response: Response from backend (ModelResponse or streaming data)
            is_streaming: Whether this is a streaming response
        """
        response_data = response.model_dump() if hasattr(response, "model_dump") else str(response)

        await self.obs_ctx.emit_event(
            event_type="luthien.backend.response",
            data={
                "is_streaming": is_streaming,
                "response": response_data,
            },
        )
        logger.debug(f"[{self.transaction_id}] Tracked backend response")

    async def track_client_response(
        self,
        response: Any,
        client_format: str,
    ) -> None:
        """Track final response sent to client.

        Args:
            response: Final response sent to client
            client_format: Format of outgoing response (anthropic or openai)
        """
        response_data = response.model_dump() if hasattr(response, "model_dump") else response

        await self.obs_ctx.emit_event(
            event_type="luthien.response.outgoing",
            data={
                "format": client_format,
                "response": response_data,
            },
        )
        logger.debug(f"[{self.transaction_id}] Tracked outgoing {client_format} response")


__all__ = ["LuthienTransaction"]
