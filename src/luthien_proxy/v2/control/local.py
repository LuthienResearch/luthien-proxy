# ABOUTME: Local (in-process) implementation of control plane service
# ABOUTME: Directly calls policy methods and integrates with DB/Redis

"""Local implementation of control plane service.

This implementation runs the control logic in-process with the API gateway.
It directly calls policy methods and integrates with database and Redis.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, AsyncIterator, Optional

from luthien_proxy.types import JSONObject
from luthien_proxy.v2.control.models import PolicyResult, RequestMetadata, StreamAction, StreamingContext

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

    async def apply_request_policies(
        self,
        request_data: dict,
        metadata: RequestMetadata,
    ) -> PolicyResult[dict]:
        """Apply policies to incoming request before LLM call."""
        try:
            # Apply policy transformation
            transformed = await self.policy.apply_request_policies(request_data)

            # Log to database
            await self.log_debug_event(
                "request_policy",
                {
                    "call_id": metadata.call_id,
                    "original": request_data,
                    "transformed": transformed,
                    "metadata": metadata.to_dict(),
                },
            )

            # Publish activity
            # TODO: Implement activity event publishing

            return PolicyResult(
                value=transformed,
                allowed=True,
                metadata={"call_id": metadata.call_id},
            )

        except Exception as exc:
            logger.error("Policy execution failed for request: %s", exc)
            # Log the error
            await self.log_debug_event(
                "request_policy_error",
                {
                    "call_id": metadata.call_id,
                    "error": str(exc),
                    "metadata": metadata.to_dict(),
                },
            )

            return PolicyResult(
                value=request_data,
                allowed=False,
                reason=str(exc),
                metadata={"call_id": metadata.call_id},
            )

    async def apply_response_policy(
        self,
        response: ModelResponse,
        metadata: RequestMetadata,
    ) -> PolicyResult[ModelResponse]:
        """Apply policies to complete response after LLM call."""
        try:
            # Apply policy transformation
            transformed = await self.policy.apply_response_policy(response)

            # Log to database
            await self.log_debug_event(
                "response_policy",
                {
                    "call_id": metadata.call_id,
                    "response": response.model_dump() if hasattr(response, "model_dump") else {},
                    "metadata": metadata.to_dict(),
                },
            )

            # Publish activity
            # TODO: Implement activity event publishing

            return PolicyResult(
                value=transformed,
                allowed=True,
                metadata={"call_id": metadata.call_id},
            )

        except Exception as exc:
            logger.error("Policy execution failed for response: %s", exc)
            await self.log_debug_event(
                "response_policy_error",
                {
                    "call_id": metadata.call_id,
                    "error": str(exc),
                    "metadata": metadata.to_dict(),
                },
            )

            return PolicyResult(
                value=response,
                allowed=True,  # Don't block response on policy errors
                reason=str(exc),
                metadata={"call_id": metadata.call_id},
            )

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

        # Log stream start
        await self.log_debug_event(
            "stream_start",
            {
                "stream_id": stream_id,
                "call_id": metadata.call_id,
                "metadata": metadata.to_dict(),
            },
        )

        return context

    async def process_streaming_chunk(
        self,
        chunk: ModelResponse,
        context: StreamingContext,
    ) -> AsyncIterator[PolicyResult[ModelResponse]]:
        """Process a streaming chunk through policies.

        Note: This is called by the gateway for each incoming chunk.
        The policy may emit zero, one, or many outgoing chunks per incoming chunk.
        """
        import asyncio

        from luthien_proxy.v2.policies.base import StreamControl

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
                await self.log_debug_event(
                    "stream_abort",
                    {
                        "stream_id": context.stream_id,
                        "call_id": context.call_id,
                        "chunk_count": context.chunk_count,
                        "reason": control.metadata.get("abort_reason", "Policy requested abort"),
                    },
                )

            # Yield all chunks from queue
            while not outgoing_queue.empty():
                outgoing_chunk = await outgoing_queue.get()
                yield PolicyResult(
                    value=outgoing_chunk,
                    allowed=True,
                    metadata={
                        "stream_id": context.stream_id,
                        "chunk_index": context.chunk_count,
                        "action": action.value,
                    },
                )

        except Exception as exc:
            logger.error("Streaming policy error: %s", exc)
            await self.log_debug_event(
                "stream_chunk_error",
                {
                    "stream_id": context.stream_id,
                    "call_id": context.call_id,
                    "chunk_count": context.chunk_count,
                    "error": str(exc),
                },
            )

            # On error, pass through the original chunk
            yield PolicyResult(
                value=chunk,
                allowed=True,
                reason=f"Policy error: {exc}",
                metadata={"stream_id": context.stream_id},
            )

    async def publish_activity(self, event) -> None:
        """Publish activity event for UI consumption."""
        if self.redis_client is None:
            return

        try:
            # TODO: Implement actual activity publishing
            # await redis_client.publish(channel, event.to_dict())
            pass
        except Exception as exc:
            logger.warning("Failed to publish activity event: %s", exc)

    async def log_debug_event(
        self,
        debug_type: str,
        payload: JSONObject,
    ) -> None:
        """Log debug event to database."""
        if self.db_pool is None:
            return

        try:
            # TODO: Implement actual database logging
            # await db_pool.execute("INSERT INTO debug_logs ...")
            pass
        except Exception as exc:
            logger.warning("Failed to log debug event: %s", exc)


__all__ = ["ControlPlaneLocal"]
