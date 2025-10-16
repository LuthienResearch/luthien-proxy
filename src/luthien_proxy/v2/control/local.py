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
from collections import defaultdict
from typing import TYPE_CHECKING, AsyncIterator, Optional

from luthien_proxy.v2.control.models import PolicyEvent, RequestMetadata
from luthien_proxy.v2.messages import FullResponse, Request, StreamingResponse
from luthien_proxy.v2.streaming import ChunkQueue

if TYPE_CHECKING:
    from luthien_proxy.utils import db
    from luthien_proxy.utils.redis_client import RedisClient
    from luthien_proxy.v2.policies.base import PolicyHandler

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
    ) -> AsyncIterator[StreamingResponse]:
        """Apply policies to streaming responses with queue-based reactive processing.

        This bridges the policy's queue-based interface with the gateway's async iterator:
        1. Creates incoming/outgoing ChunkQueues
        2. Launches policy's reactive task as background task
        3. Feeds chunks from LLM into incoming queue
        4. Yields chunks from outgoing queue to client
        """
        # Set call ID for event emission
        self.policy.set_call_id(metadata.call_id)

        # Emit stream start event
        start_event = PolicyEvent(
            event_type="stream_start",
            call_id=metadata.call_id,
            summary="Started processing stream",
            details={},
            severity="info",
        )
        self._handle_policy_event(start_event)

        # Create queues for policy communication
        incoming_queue: ChunkQueue[StreamingResponse] = ChunkQueue()
        outgoing_queue: ChunkQueue[StreamingResponse] = ChunkQueue()

        chunk_count = 0
        policy_task = None
        feed_task = None

        try:
            # Launch policy's reactive task in background
            policy_task = asyncio.create_task(self.policy.process_streaming_response(incoming_queue, outgoing_queue))

            # Feed incoming chunks to policy while yielding outgoing chunks
            # We need to run both producer (feeding incoming) and consumer (yielding outgoing) concurrently

            async def feed_incoming():
                """Feed chunks from LLM into incoming queue."""
                try:
                    async for chunk in incoming:
                        await incoming_queue.put(chunk)
                finally:
                    await incoming_queue.close()

            # Start feeding task
            feed_task = asyncio.create_task(feed_incoming())

            # Yield chunks from outgoing queue while monitoring policy task
            while True:
                # Race between getting chunks and policy task completion
                get_task = asyncio.create_task(outgoing_queue.get_available())
                done, pending = await asyncio.wait([get_task, policy_task], return_when=asyncio.FIRST_COMPLETED)

                # Check if policy_task completed (possibly with error)
                if policy_task in done:
                    # Policy task finished - check for exceptions
                    try:
                        await policy_task
                    except Exception:
                        # Cancel the get_task if it's still pending
                        if get_task in pending:
                            get_task.cancel()
                            try:
                                await get_task
                            except asyncio.CancelledError:
                                pass
                        raise  # Re-raise policy exception

                    # Policy completed successfully - get final batch if any
                    if get_task in pending:
                        batch = await get_task
                    else:
                        batch = get_task.result()

                    if batch:
                        for chunk in batch:
                            chunk_count += 1
                            yield chunk
                    break

                # get_task completed first - yield the batch
                batch = get_task.result()
                if not batch:  # Stream ended
                    break

                for chunk in batch:
                    chunk_count += 1
                    yield chunk

            # Wait for feed task to complete
            await feed_task

            # Emit stream complete event
            complete_event = PolicyEvent(
                event_type="stream_complete",
                call_id=metadata.call_id,
                summary=f"Completed stream with {chunk_count} chunks",
                details={"chunk_count": chunk_count},
                severity="info",
            )
            self._handle_policy_event(complete_event)

        except Exception as exc:
            logger.error(f"Streaming policy error: {exc}")

            error_event = PolicyEvent(
                event_type="stream_error",
                call_id=metadata.call_id,
                summary=f"Error during streaming after {chunk_count} chunks: {exc}",
                details={"chunk_count": chunk_count, "error": str(exc), "error_type": type(exc).__name__},
                severity="error",
            )
            self._handle_policy_event(error_event)

            # Cancel policy task if still running
            if policy_task and not policy_task.done():
                policy_task.cancel()
                try:
                    await policy_task
                except asyncio.CancelledError:
                    pass

            # Cancel feed task if still running (it might be blocked waiting to feed chunks)
            if feed_task and not feed_task.done():
                feed_task.cancel()
                try:
                    await feed_task
                except asyncio.CancelledError:
                    pass

            # Re-raise - let gateway handle it
            raise

    async def get_events(self, call_id: str) -> list[PolicyEvent]:
        """Get all events for a specific call."""
        return self._events.get(call_id, [])


__all__ = ["ControlPlaneLocal"]
