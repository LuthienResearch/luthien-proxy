# ABOUTME: Local (in-process) implementation of control plane service
# ABOUTME: Executes policy methods with OpenTelemetry tracing and proper error handling

"""Local implementation of control plane service.

This implementation runs the control logic in-process with the API gateway.
It executes policy methods with OpenTelemetry spans for distributed tracing,
and delegates streaming coordination to StreamingOrchestrator.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, AsyncIterator

from opentelemetry import trace

from luthien_proxy.v2.control.models import StreamingError
from luthien_proxy.v2.control.streaming import StreamingOrchestrator
from luthien_proxy.v2.messages import FullResponse, Request, StreamingResponse
from luthien_proxy.v2.policies.context import PolicyContext
from luthien_proxy.v2.storage.events import emit_response_event, reconstruct_full_response_from_chunks

if TYPE_CHECKING:
    from luthien_proxy.utils import db, redis_client
    from luthien_proxy.v2.observability import SimpleEventPublisher
    from luthien_proxy.v2.policies.base import LuthienPolicy

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


class ControlPlaneLocal:
    """In-process implementation of control plane service.

    This implementation runs the control logic in-process with the API gateway.
    Uses OpenTelemetry for distributed tracing instead of custom event collection.

    Responsibilities:
    - Execute policy methods with proper PolicyContext (including OTel spans)
    - Handle errors and record them as span events
    - Delegate streaming coordination to StreamingOrchestrator
    """

    def __init__(
        self,
        policy: LuthienPolicy,
        event_publisher: SimpleEventPublisher | None = None,
    ):
        """Initialize local control plane.

        Args:
            policy: The policy handler to execute
            event_publisher: Optional publisher for real-time UI events
        """
        self.policy = policy
        self.event_publisher = event_publisher

        # Streaming orchestrator for stream processing
        self.streaming_orchestrator = StreamingOrchestrator()

    async def process_request(
        self,
        request: Request,
        call_id: str,
    ) -> Request:
        """Apply policies to incoming request before LLM call."""
        with tracer.start_as_current_span("control_plane.process_request") as span:
            # Add span attributes
            span.set_attribute("luthien.call_id", call_id)
            span.set_attribute("luthien.model", request.model)
            if request.max_tokens:
                span.set_attribute("luthien.max_tokens", request.max_tokens)

            # Create context for this request
            context = PolicyContext(
                call_id=call_id,
                span=span,
                event_publisher=self.event_publisher,
            )

            try:
                # Apply policy transformation
                transformed = await self.policy.process_request(request, context)
                span.set_attribute("luthien.policy.success", True)
                return transformed

            except Exception as exc:
                logger.error(f"Policy execution failed for request: {exc}")

                # Record error in span
                span.set_attribute("luthien.policy.success", False)
                span.set_attribute("luthien.policy.error_type", type(exc).__name__)
                span.add_event(
                    "request_policy_error",
                    attributes={
                        "error.type": type(exc).__name__,
                        "error.message": str(exc),
                    },
                )

                # Re-raise to let gateway handle it
                raise

    async def process_full_response(
        self,
        response: FullResponse,
        call_id: str,
    ) -> FullResponse:
        """Apply policies to complete response after LLM call."""
        with tracer.start_as_current_span("control_plane.process_full_response") as span:
            # Add span attributes
            span.set_attribute("luthien.call_id", call_id)
            span.set_attribute("luthien.stream.enabled", False)

            # Create context for this response
            context = PolicyContext(
                call_id=call_id,
                span=span,
                event_publisher=self.event_publisher,
            )

            try:
                # Apply policy transformation
                transformed = await self.policy.process_full_response(response, context)
                span.set_attribute("luthien.policy.success", True)
                return transformed

            except Exception as exc:
                logger.error(f"Policy execution failed for response: {exc}")

                # Record error in span
                span.set_attribute("luthien.policy.success", False)
                span.set_attribute("luthien.policy.error_type", type(exc).__name__)
                span.add_event(
                    "response_policy_error",
                    attributes={
                        "error.type": type(exc).__name__,
                        "error.message": str(exc),
                    },
                )

                # Return original response (don't block response on policy error)
                return response

    async def process_streaming_response(
        self,
        incoming: AsyncIterator[StreamingResponse],
        call_id: str,
        timeout_seconds: float = 30.0,
        db_pool: db.DatabasePool | None = None,
        redis_conn: redis_client.RedisClient | None = None,
    ) -> AsyncIterator[StreamingResponse]:
        """Apply policies to streaming responses with queue-based reactive processing.

        This uses StreamingOrchestrator to bridge the policy's queue-based interface
        with the gateway's async iterator. It also buffers chunks to emit conversation
        events after streaming completes.

        Args:
            incoming: Async iterator of streaming responses from LLM
            call_id: Unique identifier for this request/response cycle
            timeout_seconds: Maximum seconds without activity before timing out (default: 30)
            db_pool: Optional database pool for event persistence
            redis_conn: Optional Redis connection for real-time pub/sub

        Yields:
            Processed streaming responses from the policy

        Raises:
            StreamingError: If streaming fails or times out
        """
        with tracer.start_as_current_span("control_plane.process_streaming_response") as span:
            # Add span attributes
            span.set_attribute("luthien.call_id", call_id)
            span.set_attribute("luthien.stream.enabled", True)
            span.set_attribute("luthien.stream.timeout_seconds", timeout_seconds)

            # Create context for this stream
            context = PolicyContext(
                call_id=call_id,
                span=span,
                event_publisher=self.event_publisher,
            )

            span.add_event("stream_start")

            chunk_count = 0
            original_chunks: list[StreamingResponse] = []

            # Wrapper to buffer original chunks as they come in
            async def buffering_incoming() -> AsyncIterator[StreamingResponse]:
                """Buffer incoming chunks while yielding them."""
                nonlocal chunk_count
                async for chunk in incoming:
                    original_chunks.append(chunk)

                    # Publish chunk event (Redis only, no DB persistence - fire and forget)
                    if self.event_publisher:
                        # Extract text content from chunk for preview
                        try:
                            chunk_dict = chunk.chunk.model_dump() if hasattr(chunk.chunk, "model_dump") else chunk.chunk
                            choices = chunk_dict.get("choices", [])
                            delta = choices[0].get("delta", {}) if choices else {}
                            content = delta.get("content", "")
                            finish_reason = choices[0].get("finish_reason") if choices else None

                            # Fire and forget - don't await
                            asyncio.create_task(
                                self.event_publisher.publish_event(
                                    call_id=call_id,
                                    event_type="streaming.chunk_received",
                                    data={
                                        "call_id": call_id,
                                        "chunk_index": len(original_chunks) - 1,
                                        "content_preview": content[:100] if content else None,
                                        "finish_reason": finish_reason,
                                    },
                                )
                            )
                        except Exception as e:
                            logger.debug(f"Failed to publish chunk event: {e}")

                    yield chunk

            # Callback to emit events after streaming completes
            async def emit_streaming_events(final_chunks: list[StreamingResponse]) -> None:
                """Emit response event with original and final chunks."""
                # Reconstruct full responses from buffered chunks
                original_response_dict = reconstruct_full_response_from_chunks(original_chunks)
                final_response_dict = reconstruct_full_response_from_chunks(final_chunks)

                # Publish full original response to Redis (for real-time UI - fire and forget)
                if self.event_publisher:
                    try:
                        original_content = ""
                        if original_response_dict.get("choices"):
                            message = original_response_dict["choices"][0].get("message", {})
                            original_content = message.get("content", "")

                        asyncio.create_task(
                            self.event_publisher.publish_event(
                                call_id=call_id,
                                event_type="streaming.original_complete",
                                data={
                                    "call_id": call_id,
                                    "content": original_content,  # Full content, not preview
                                    "total_chunks": len(original_chunks),
                                    "finish_reason": original_response_dict.get("choices", [{}])[0].get(
                                        "finish_reason"
                                    ),
                                },
                            )
                        )
                    except Exception as e:
                        logger.debug(f"Failed to publish original complete event: {e}")

                # Publish full transformed response to Redis (for real-time UI - fire and forget)
                if self.event_publisher:
                    try:
                        final_content = ""
                        if final_response_dict.get("choices"):
                            message = final_response_dict["choices"][0].get("message", {})
                            final_content = message.get("content", "")

                        asyncio.create_task(
                            self.event_publisher.publish_event(
                                call_id=call_id,
                                event_type="streaming.transformed_complete",
                                data={
                                    "call_id": call_id,
                                    "content": final_content,  # Full content, not preview
                                    "total_chunks": len(final_chunks),
                                    "finish_reason": final_response_dict.get("choices", [{}])[0].get("finish_reason"),
                                },
                            )
                        )
                    except Exception as e:
                        logger.debug(f"Failed to publish transformed complete event: {e}")

                # Emit event to DB (non-blocking, for historical record)
                if db_pool:
                    emit_response_event(
                        call_id=call_id,
                        original_response=original_response_dict,
                        final_response=final_response_dict,
                        db_pool=db_pool,
                        redis_conn=redis_conn,
                    )
                    logger.debug(f"Emitted streaming response event for call {call_id}")

            try:
                # Use orchestrator to handle streaming with timeout monitoring
                async def policy_processor(incoming_queue, outgoing_queue, keepalive):
                    await self.policy.process_streaming_response(
                        incoming_queue, outgoing_queue, context, keepalive=keepalive
                    )

                async for chunk in self.streaming_orchestrator.process(
                    buffering_incoming(),
                    policy_processor,
                    timeout_seconds,
                    span=span,
                    on_complete=emit_streaming_events,
                ):
                    chunk_count += 1

                    # Publish outgoing chunk event (Redis only, no DB persistence - fire and forget)
                    if self.event_publisher:
                        try:
                            chunk_dict = chunk.chunk.model_dump() if hasattr(chunk.chunk, "model_dump") else chunk.chunk
                            choices = chunk_dict.get("choices", [])
                            delta = choices[0].get("delta", {}) if choices else {}
                            content = delta.get("content", "")
                            finish_reason = choices[0].get("finish_reason") if choices else None

                            # Fire and forget - don't await
                            asyncio.create_task(
                                self.event_publisher.publish_event(
                                    call_id=call_id,
                                    event_type="streaming.chunk_sent",
                                    data={
                                        "call_id": call_id,
                                        "chunk_index": chunk_count - 1,
                                        "content_preview": content[:100] if content else None,
                                        "finish_reason": finish_reason,
                                    },
                                )
                            )
                        except Exception as e:
                            logger.debug(f"Failed to publish chunk sent event: {e}")

                    yield chunk

                # Success - record completion
                span.set_attribute("luthien.stream.chunk_count", chunk_count)
                span.set_attribute("luthien.policy.success", True)
                span.add_event("stream_complete", attributes={"chunk_count": chunk_count})

            except BaseException as exc:
                # BaseException catches both regular exceptions and ExceptionGroup from TaskGroup
                logger.error(f"Streaming policy error: {exc}")

                # Record error in span
                span.set_attribute("luthien.stream.chunk_count", chunk_count)
                span.set_attribute("luthien.policy.success", False)
                span.set_attribute("luthien.policy.error_type", type(exc).__name__)
                span.add_event(
                    "stream_error",
                    attributes={
                        "error.type": type(exc).__name__,
                        "error.message": str(exc),
                        "chunk_count": chunk_count,
                    },
                )

                # Always wrap in StreamingError for consistent interface
                raise StreamingError(f"Streaming failed after {chunk_count} chunks") from exc


__all__ = ["ControlPlaneLocal"]
