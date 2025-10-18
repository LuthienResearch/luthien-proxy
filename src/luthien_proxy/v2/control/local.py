# ABOUTME: Local (in-process) implementation of control plane service
# ABOUTME: Executes policy methods with proper context and error handling

"""Local implementation of control plane service.

This implementation runs the control logic in-process with the API gateway.
It executes policy methods, provides PolicyContext, and delegates to:
- ActivityPublisher for event handling
- StreamingOrchestrator for streaming coordination
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, AsyncIterator

from luthien_proxy.v2.activity import ActivityPublisher
from luthien_proxy.v2.control.models import PolicyEvent, StreamingError
from luthien_proxy.v2.control.streaming import StreamingOrchestrator
from luthien_proxy.v2.messages import FullResponse, Request, StreamingResponse
from luthien_proxy.v2.policies.context import PolicyContext

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from luthien_proxy.v2.policies.base import LuthienPolicy

logger = logging.getLogger(__name__)


class ControlPlaneLocal:
    """In-process implementation of control plane service.

    This is the Phase 1 implementation that runs everything locally.
    In Phase 2, we might add ControlPlaneHTTP that makes network calls instead.

    Responsibilities:
    - Execute policy methods with proper PolicyContext
    - Handle errors and emit error events
    - Delegate event handling to ActivityPublisher
    - Delegate streaming coordination to StreamingOrchestrator
    """

    def __init__(
        self,
        policy: LuthienPolicy,
        redis_client: Redis | None = None,
    ):
        """Initialize local control plane.

        Args:
            policy: The policy handler to execute
            redis_client: Optional Redis client for activity publishing
        """
        self.policy = policy

        # Activity publisher for event handling
        self.activity_publisher = ActivityPublisher(redis_client)

        # Streaming orchestrator for stream processing
        self.streaming_orchestrator = StreamingOrchestrator()

    def _emit_event(
        self,
        event_type: str,
        call_id: str,
        summary: str,
        severity: str = "info",
    ) -> None:
        """Helper to create and emit a policy event."""
        event = PolicyEvent(
            event_type=event_type,
            call_id=call_id,
            summary=summary,
            severity=severity,
        )
        self.activity_publisher.handle_policy_event(event)

    async def process_request(
        self,
        request: Request,
        call_id: str,
    ) -> Request:
        """Apply policies to incoming request before LLM call."""
        # Create context for this request
        context = PolicyContext(
            call_id=call_id,
            emit_event=self.activity_publisher.handle_policy_event,
        )

        try:
            # Apply policy transformation
            transformed = await self.policy.process_request(request, context)
            return transformed

        except Exception as exc:
            logger.error(f"Policy execution failed for request: {exc}")

            # Emit error event
            self._emit_event(
                event_type="request_policy_error",
                call_id=call_id,
                summary=f"Policy failed to process request: {exc}; ErrorType: {type(exc).__name__}",
                severity="error",
            )

            # Re-raise to let gateway handle it
            raise

    async def process_full_response(
        self,
        response: FullResponse,
        call_id: str,
    ) -> FullResponse:
        """Apply policies to complete response after LLM call."""
        # Create context for this response
        context = PolicyContext(
            call_id=call_id,
            emit_event=self.activity_publisher.handle_policy_event,
        )

        try:
            # Apply policy transformation
            transformed = await self.policy.process_full_response(response, context)
            return transformed

        except Exception as exc:
            logger.error(f"Policy execution failed for response: {exc}")

            # Emit error event
            self._emit_event(
                event_type="response_policy_error",
                call_id=call_id,
                summary=f"Policy failed to process response: {exc}; ErrorType: {type(exc).__name__}",
                severity="error",
            )

            # Return original response (don't block response on policy error)
            return response

    async def process_streaming_response(
        self,
        incoming: AsyncIterator[StreamingResponse],
        call_id: str,
        timeout_seconds: float = 30.0,
    ) -> AsyncIterator[StreamingResponse]:
        """Apply policies to streaming responses with queue-based reactive processing.

        This uses StreamingOrchestrator to bridge the policy's queue-based interface
        with the gateway's async iterator.

        Args:
            incoming: Async iterator of streaming responses from LLM
            call_id: Unique identifier for this request/response cycle
            timeout_seconds: Maximum seconds without activity before timing out (default: 30)

        Yields:
            Processed streaming responses from the policy

        Raises:
            StreamingError: If streaming fails or times out
        """
        # Create context for this stream
        context = PolicyContext(
            call_id=call_id,
            emit_event=self.activity_publisher.handle_policy_event,
        )

        self._emit_event("stream_start", call_id, "Started processing stream")

        chunk_count = 0

        try:
            # Use orchestrator to handle streaming with timeout monitoring
            async def policy_processor(incoming_queue, outgoing_queue, keepalive):
                await self.policy.process_streaming_response(
                    incoming_queue, outgoing_queue, context, keepalive=keepalive
                )

            async for chunk in self.streaming_orchestrator.process(incoming, policy_processor, timeout_seconds):
                chunk_count += 1
                yield chunk

            # Success - emit completion event
            self._emit_event("stream_complete", call_id, f"Completed stream with {chunk_count} chunks")

        except BaseException as exc:
            # BaseException catches both regular exceptions and ExceptionGroup from TaskGroup
            logger.error(f"Streaming policy error: {exc}")

            self._emit_event(
                "stream_error",
                call_id,
                f"Error during streaming after {chunk_count} chunks: {exc}; ErrorType: {type(exc).__name__}",
                severity="error",
            )

            # Always wrap in StreamingError for consistent interface
            raise StreamingError(f"Streaming failed after {chunk_count} chunks") from exc


__all__ = ["ControlPlaneLocal"]
