# ABOUTME: Local (in-process) implementation of control plane service
# ABOUTME: Directly calls policy methods, collects events, integrates with DB/Redis

"""Local implementation of control plane service.

This implementation runs the control logic in-process with the API gateway.
It directly calls policy methods, collects PolicyEvents, and integrates with
database and Redis.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import TYPE_CHECKING, AsyncIterator, Optional

from luthien_proxy.v2.activity import ActivityPublisher, PolicyEventEmitted
from luthien_proxy.v2.control.models import PolicyEvent, RequestMetadata, StreamingError
from luthien_proxy.v2.messages import FullResponse, Request, StreamingResponse
from luthien_proxy.v2.streaming import ChunkQueue

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from luthien_proxy.utils import db
    from luthien_proxy.v2.policies.base import PolicyHandler

logger = logging.getLogger(__name__)


class TimeoutTracker:
    """Tracks activity and provides timeout monitoring for streaming operations."""

    def __init__(self, timeout_seconds: float):
        self.timeout_seconds = timeout_seconds
        self.last_activity = time.time()

    def ping(self) -> None:
        """Record activity (resets timeout timer)."""
        self.last_activity = time.time()

    async def raise_on_timeout(self) -> None:
        """Monitor task that raises StreamingError if timeout exceeded.

        Runs until cancelled (when streaming completes successfully) or until
        timeout is exceeded (raises StreamingError).
        """
        while True:
            await asyncio.sleep(1.0)
            elapsed = time.time() - self.last_activity
            if elapsed > self.timeout_seconds:
                raise StreamingError(f"Policy timeout: no activity for {self.timeout_seconds}s")


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
        redis_client: Optional[Redis] = None,
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

        # Activity publisher for real-time events
        self.activity_publisher = ActivityPublisher(redis_client)

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

        # Publish to activity stream
        # Note: We create a task but don't await it to avoid blocking the policy
        asyncio.create_task(
            self.activity_publisher.publish(
                PolicyEventEmitted(
                    call_id=event.call_id,
                    trace_id=None,  # TODO: Get from metadata
                    policy_name=event.event_type.split("_")[0],  # Extract from event type
                    event_name=event.event_type,
                    description=event.summary,
                    data=event.details,
                    phase="request",  # TODO: Track current phase
                )
            )
        )

        # TODO: Log to database

    async def process_request(
        self,
        request: Request,
        metadata: RequestMetadata,
    ) -> Request:
        """Apply policies to incoming request before LLM call."""
        # Set call ID for event emission
        self.policy.set_call_id(metadata.call_id)

        try:
            # Apply policy transformation
            transformed = await self.policy.process_request(request)
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

    async def process_full_response(
        self,
        response: FullResponse,
        metadata: RequestMetadata,
    ) -> FullResponse:
        """Apply policies to complete response after LLM call."""
        # Set call ID for event emission
        self.policy.set_call_id(metadata.call_id)

        try:
            # Apply policy transformation
            transformed = await self.policy.process_full_response(response)
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

    async def process_streaming_response(
        self,
        incoming: AsyncIterator[StreamingResponse],
        metadata: RequestMetadata,
        timeout_seconds: float = 30.0,
    ) -> AsyncIterator[StreamingResponse]:
        """Apply policies to streaming responses with queue-based reactive processing.

        This bridges the policy's queue-based interface with the gateway's async iterator:
        1. Creates incoming/outgoing ChunkQueues
        2. Launches background tasks to feed incoming queue and run policy
        3. Monitors for timeout (no activity for timeout_seconds)
        4. Yields chunks from outgoing queue to client

        The policy's finally block ensures the outgoing queue is always closed,
        allowing this method to drain the queue and detect completion naturally.

        Args:
            incoming: Async iterator of streaming responses from LLM
            metadata: Request metadata including call_id
            timeout_seconds: Maximum seconds without activity before timing out (default: 30)
        """
        # Set call ID for event emission
        self.policy.set_call_id(metadata.call_id)

        # Emit stream start event
        self._handle_policy_event(
            PolicyEvent(
                event_type="stream_start",
                call_id=metadata.call_id,
                summary="Started processing stream",
                details={},
                severity="info",
            )
        )

        # Create queues for policy communication
        incoming_queue: ChunkQueue[StreamingResponse] = ChunkQueue()
        outgoing_queue: ChunkQueue[StreamingResponse] = ChunkQueue()

        # Create timeout tracker
        timeout_tracker = TimeoutTracker(timeout_seconds)

        chunk_count = 0

        try:
            async with asyncio.TaskGroup() as tg:
                # Launch background tasks
                tg.create_task(self._feed_incoming_chunks(incoming, incoming_queue))
                tg.create_task(
                    self.policy.process_streaming_response(
                        incoming_queue, outgoing_queue, keepalive=timeout_tracker.ping
                    )
                )
                monitor_task = tg.create_task(timeout_tracker.raise_on_timeout())

                # Drain outgoing queue until policy closes it
                while True:
                    batch = await outgoing_queue.get_available()
                    if not batch:  # Queue closed by policy's finally block
                        break

                    timeout_tracker.ping()

                    for chunk in batch:
                        chunk_count += 1
                        yield chunk

                # Cancel timeout monitor - streaming completed successfully
                monitor_task.cancel()

                # TaskGroup waits for all tasks to complete when exiting this block

            # Success - emit completion event
            self._handle_policy_event(
                PolicyEvent(
                    event_type="stream_complete",
                    call_id=metadata.call_id,
                    summary=f"Completed stream with {chunk_count} chunks",
                    details={"chunk_count": chunk_count},
                    severity="info",
                )
            )

        except BaseException as exc:
            # BaseException catches both regular exceptions and ExceptionGroup from TaskGroup
            logger.error(f"Streaming policy error: {exc}")

            self._handle_policy_event(
                PolicyEvent(
                    event_type="stream_error",
                    call_id=metadata.call_id,
                    summary=f"Error during streaming after {chunk_count} chunks: {exc}",
                    details={
                        "chunk_count": chunk_count,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
                    severity="error",
                )
            )

            # TaskGroup automatically cancels all tasks on exception
            # Always wrap in StreamingError for consistent interface
            raise StreamingError(f"Streaming failed after {chunk_count} chunks") from exc

    async def _feed_incoming_chunks(
        self,
        source: AsyncIterator[StreamingResponse],
        queue: ChunkQueue[StreamingResponse],
    ) -> None:
        """Feed chunks from source iterator into queue, then close it.

        This runs as a background task, continuously pulling from the source
        iterator and pushing into the queue until the source is exhausted.
        """
        try:
            async for chunk in source:
                await queue.put(chunk)
        finally:
            await queue.close()

    async def get_events(self, call_id: str) -> list[PolicyEvent]:
        """Get all events for a specific call."""
        return self._events.get(call_id, [])


__all__ = ["ControlPlaneLocal"]
