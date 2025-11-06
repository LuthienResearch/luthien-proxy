# ABOUTME: Simplified PolicyOrchestrator using explicit queue-based streaming pipeline
# ABOUTME: Dependencies injected for policy execution and client formatting, recording at boundaries

"""Simplified policy orchestration with explicit streaming pipeline.

This refactored PolicyOrchestrator uses dependency injection
and explicit queues to create a clear, typed streaming pipeline.
"""

import asyncio
import logging
from typing import AsyncIterator

from litellm.types.utils import ModelResponse

from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.observability.context import ObservabilityContext
from luthien_proxy.v2.observability.transaction_recorder import TransactionRecorder
from luthien_proxy.v2.policies.policy import PolicyProtocol
from luthien_proxy.v2.policies.policy_context import PolicyContext
from luthien_proxy.v2.streaming.client_formatter import ClientFormatter
from luthien_proxy.v2.streaming.policy_executor import PolicyExecutor

logger = logging.getLogger(__name__)


class PolicyOrchestrator:
    """Orchestrates request/response flow with explicit streaming pipeline.

    This orchestrator uses dependency injection to decouple pipeline stages:
    - Policy: Contains business logic for request/response transformation
    - PolicyExecutor: Block assembly + policy hook invocation (ModelResponse → ModelResponse)
    - ClientFormatter: Common format → client-specific SSE (ModelResponse → str)

    The streaming pipeline is explicit, with typed queues connecting stages
    and TransactionRecorder wrapping stages at common format boundaries.

    Note: Backend streams from LiteLLM are already in common format (ModelResponse),
    so no ingress formatting is needed.
    """

    def __init__(
        self,
        policy: PolicyProtocol,
        policy_executor: PolicyExecutor,
        client_formatter: ClientFormatter,
        transaction_recorder: TransactionRecorder,
        queue_size: int = 10000,
    ) -> None:
        """Initialize orchestrator with injected dependencies.

        Args:
            policy: Policy instance implementing PolicyProtocol with request/response hooks
            policy_executor: Executes policy logic on ModelResponse chunks
            client_formatter: Converts ModelResponse to client SSE strings
            transaction_recorder: Records chunks at pipeline boundaries
            queue_size: Maximum queue size (circuit breaker on overflow)
        """
        self.policy = policy
        self.policy_executor = policy_executor
        self.client_formatter = client_formatter
        self.transaction_recorder = transaction_recorder
        self.queue_size = queue_size

    async def process_request(
        self,
        request: Request,
        policy_ctx: PolicyContext,
        obs_ctx: ObservabilityContext,
    ) -> Request:
        """Apply policy to request before backend invocation.

        This processes the request through policy hooks before sending
        to the backend LLM. The policy_ctx is shared with streaming
        response processing.

        Args:
            request: Incoming request from client
            policy_ctx: Policy context (shared with response processing)
            obs_ctx: Observability context for tracing

        Returns:
            Policy-modified request to send to backend

        Raises:
            PolicyError: If policy rejects the request
        """
        # Set request in context for policy access
        policy_ctx.request = request

        # Call policy's on_request hook
        return await self.policy.on_request(request, policy_ctx)

    async def process_streaming_response(
        self,
        backend_stream: AsyncIterator[ModelResponse],
        policy_ctx: PolicyContext,
        obs_ctx: ObservabilityContext,
    ) -> AsyncIterator[str]:
        """Process streaming response through policy pipeline.

        This creates the explicit queue-based pipeline:
        1. backend_stream (ModelResponse) → PolicyExecutor (recorded) → policy_out_queue
        2. policy_out_queue → ClientFormatter (recorded) → sse_queue
        3. Drain sse_queue and yield to client

        Args:
            backend_stream: Streaming ModelResponse from backend LLM (already common format)
            policy_ctx: Policy context (shared with request processing)
            obs_ctx: Observability context for tracing

        Yields:
            SSE formatted strings in client-specific format

        Raises:
            PolicyTimeoutError: If policy processing times out
            QueueFullError: If any queue exceeds circuit breaker limit
            Exception: On pipeline errors (propagated from background tasks)
        """
        # Create typed queues that define pipeline contracts
        # Note: Queues use None as sentinel to signal end of stream
        policy_out_queue: asyncio.Queue[ModelResponse | None] = asyncio.Queue(maxsize=self.queue_size)
        sse_queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=self.queue_size)

        # Launch pipeline stages using TaskGroup to ensure error propagation
        # TaskGroup ensures that exceptions from background tasks are caught and raised
        # TODO: Re-add transaction_recorder.wrap() once we implement it properly
        async with asyncio.TaskGroup() as tg:
            # Start policy executor task
            tg.create_task(
                self.policy_executor.process(
                    backend_stream,
                    policy_out_queue,
                    self.policy,  # Pass policy to executor
                    policy_ctx,
                    obs_ctx,
                )
            )
            # Start client formatter task
            tg.create_task(
                self.client_formatter.process(
                    policy_out_queue,
                    sse_queue,
                    policy_ctx,
                    obs_ctx,
                )
            )

            # Drain final queue and yield to client while tasks run
            # If either task fails, TaskGroup will cancel remaining tasks and raise
            async for event in self._drain_queue(sse_queue):
                yield event

    async def _drain_queue(self, queue: asyncio.Queue[str | None]) -> AsyncIterator[str]:
        """Drain queue until shutdown.

        Args:
            queue: Queue to drain

        Yields:
            SSE strings from queue until None sentinel
        """
        while True:
            event = await queue.get()
            if event is None:
                # None sentinel signals end of stream
                break
            # DEBUG: Log raw SSE string being sent to client
            logger.debug(f"[CLIENT OUT] {event[:200]}")  # Truncate for readability
            yield event


class QueueFullError(Exception):
    """Raised when a pipeline queue exceeds circuit breaker limit."""

    pass


__all__ = ["PolicyOrchestrator", "QueueFullError"]
