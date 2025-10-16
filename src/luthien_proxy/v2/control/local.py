# ABOUTME: Local (in-process) implementation of control plane service
# ABOUTME: Directly calls policy methods, collects events, integrates with DB/Redis

"""Local implementation of control plane service.

This implementation runs the control logic in-process with the API gateway.
It directly calls policy methods, collects PolicyEvents, and integrates with
database and Redis.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from typing import TYPE_CHECKING, AsyncIterator, Optional

from luthien_proxy.v2.control.models import PolicyEvent, RequestMetadata, StreamAction, StreamingContext

if TYPE_CHECKING:
    from typing import Any

    from luthien_proxy.utils import db
    from luthien_proxy.utils.redis_client import RedisClient
    from luthien_proxy.v2.policies.base import PolicyHandler

    ModelResponse = Any  # LiteLLM's ModelResponse has incomplete type annotations

logger = logging.getLogger(__name__)


class ControlPlaneLocal:
    """In-process implementation of control plane service.

    This is the Phase 1 implementation that runs everything locally.
    In Phase 2, we might add ControlPlaneHTTP that makes network calls instead.

    Responsibilities:
    - Execute policy methods
    - Collect PolicyEvents emitted by policies
    - Log events to database
    - Publish events to Redis for UI
    """

    def __init__(
        self,
        policy: PolicyHandler,
        db_pool: Optional[db.DatabasePool] = None,
        redis_client: Optional[RedisClient] = None,
    ):
        """Initialize local control plane.

        Args:
            policy: The policy handler to execute
            db_pool: Optional database pool for logging
            redis_client: Optional Redis client for activity publishing
        """
        self.policy = policy
        self.db_pool = db_pool
        self.redis_client = redis_client

        # In-memory event storage (keyed by call_id)
        self._events: dict[str, list[PolicyEvent]] = defaultdict(list)

        # Set up event handler for the policy
        self.policy.set_event_handler(self._handle_policy_event)

    def _handle_policy_event(self, event: PolicyEvent) -> None:
        """Handle a policy event emission.

        This is called by the policy whenever it emits an event.
        """
        # Store in memory
        self._events[event.call_id].append(event)

        # Log to console
        logger.info(
            f"[{event.severity.upper()}] {event.event_type}: {event.summary}",
            extra={"call_id": event.call_id, "details": event.details},
        )

        # TODO: Log to database
        # TODO: Publish to Redis for UI

    async def apply_request_policies(
        self,
        request_data: dict,
        metadata: RequestMetadata,
    ) -> dict:
        """Apply policies to incoming request before LLM call."""
        # Set call ID for event emission
        self.policy.set_call_id(metadata.call_id)

        try:
            # Apply policy transformation
            transformed = await self.policy.apply_request_policies(request_data)
            return transformed

        except Exception as exc:
            logger.error(f"Policy execution failed for request: {exc}")

            # Create an error event
            error_event = PolicyEvent(
                event_type="request_policy_error",
                call_id=metadata.call_id,
                summary=f"Policy failed to process request: {exc}",
                details={"error": str(exc), "error_type": type(exc).__name__},
                severity="error",
            )
            self._handle_policy_event(error_event)

            # Re-raise to let gateway handle it
            raise

    async def apply_response_policy(
        self,
        response: ModelResponse,
        metadata: RequestMetadata,
    ) -> ModelResponse:
        """Apply policies to complete response after LLM call."""
        # Set call ID for event emission
        self.policy.set_call_id(metadata.call_id)

        try:
            # Apply policy transformation
            transformed = await self.policy.apply_response_policy(response)
            return transformed

        except Exception as exc:
            logger.error(f"Policy execution failed for response: {exc}")

            # Create an error event
            error_event = PolicyEvent(
                event_type="response_policy_error",
                call_id=metadata.call_id,
                summary=f"Policy failed to process response: {exc}",
                details={"error": str(exc), "error_type": type(exc).__name__},
                severity="error",
            )
            self._handle_policy_event(error_event)

            # Return original response (don't block response on policy error)
            return response

    async def create_streaming_context(
        self,
        request_data: dict,
        metadata: RequestMetadata,
    ) -> StreamingContext:
        """Initialize streaming context and return stream ID."""
        stream_id = str(uuid.uuid4())

        context = StreamingContext(
            stream_id=stream_id,
            call_id=metadata.call_id,
            request_data=request_data,
            policy_state={},
            chunk_count=0,
        )

        # Create stream start event
        start_event = PolicyEvent(
            event_type="stream_start",
            call_id=metadata.call_id,
            summary=f"Started stream {stream_id}",
            details={"stream_id": stream_id, "model": request_data.get("model")},
            severity="info",
        )
        self._handle_policy_event(start_event)

        return context

    async def process_streaming_chunk(
        self,
        chunk: ModelResponse,
        context: StreamingContext,
    ) -> AsyncIterator[ModelResponse]:
        """Process a streaming chunk through policies.

        Note: This is called by the gateway for each incoming chunk.
        The policy may emit zero, one, or many outgoing chunks per incoming chunk.
        """
        import asyncio

        from luthien_proxy.v2.policies.base import StreamControl

        # Set call ID for event emission
        self.policy.set_call_id(context.call_id)

        # Create queue and control for this chunk
        outgoing_queue: asyncio.Queue = asyncio.Queue()
        control = StreamControl()

        try:
            # Apply policy (policy puts results in queue)
            action = await self.policy.apply_streaming_chunk_policy(
                chunk,
                outgoing_queue,
                control,
            )

            context.chunk_count += 1

            # Check for abort
            if action == StreamAction.ABORT or control.should_abort:
                context.should_abort = True

                abort_event = PolicyEvent(
                    event_type="stream_abort",
                    call_id=context.call_id,
                    summary=f"Stream aborted after {context.chunk_count} chunks",
                    details={
                        "stream_id": context.stream_id,
                        "chunk_count": context.chunk_count,
                        "reason": control.metadata.get("abort_reason", "Policy requested abort"),
                    },
                    severity="warning",
                )
                self._handle_policy_event(abort_event)

            # Yield all chunks from queue
            while not outgoing_queue.empty():
                outgoing_chunk = await outgoing_queue.get()
                yield outgoing_chunk

        except Exception as exc:
            logger.error(f"Streaming policy error: {exc}")

            error_event = PolicyEvent(
                event_type="stream_chunk_error",
                call_id=context.call_id,
                summary=f"Error processing chunk {context.chunk_count}: {exc}",
                details={
                    "stream_id": context.stream_id,
                    "chunk_count": context.chunk_count,
                    "error": str(exc),
                },
                severity="error",
            )
            self._handle_policy_event(error_event)

            # On error, pass through the original chunk
            yield chunk

    async def get_events(self, call_id: str) -> list[PolicyEvent]:
        """Get all events for a specific call."""
        return self._events.get(call_id, [])


__all__ = ["ControlPlaneLocal"]
