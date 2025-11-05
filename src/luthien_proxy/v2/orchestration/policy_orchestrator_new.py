# ABOUTME: Simplified PolicyOrchestrator using explicit queue-based streaming pipeline
# ABOUTME: Dependencies injected for formatters and policy execution, recording at boundaries
# TODO: Rename to policy_orchestrator.py once old implementation is removed

"""Simplified policy orchestration with explicit streaming pipeline.

This is the refactored PolicyOrchestrator that uses dependency injection
and explicit queues to create a clear, typed streaming pipeline.

NOTE: This file is temporarily named _new.py to avoid conflicts during migration.
Once the old PolicyOrchestrator is removed, rename this to policy_orchestrator.py.
"""

import asyncio
from typing import AsyncIterator

from litellm.types.utils import ModelResponse

from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.observability.context import ObservabilityContext
from luthien_proxy.v2.observability.transaction_recorder import TransactionRecorder
from luthien_proxy.v2.streaming.client_formatter import (
    ClientFormatter,
)
from luthien_proxy.v2.streaming.common_formatter import (
    CommonFormatter,
)
from luthien_proxy.v2.streaming.policy_executor import PolicyExecutor
from luthien_proxy.v2.streaming.protocol import PolicyContext


class PolicyOrchestrator:
    """Orchestrates request/response flow with explicit streaming pipeline.

    This orchestrator uses dependency injection to decouple pipeline stages:
    - CommonFormatter: Backend-specific → common format
    - PolicyExecutor: Block assembly + policy hooks (common → common)
    - ClientFormatter: Common format → client-specific SSE

    The streaming pipeline is explicit, with typed queues connecting stages
    and TransactionRecorder wrapping stages at common format boundaries.
    """

    def __init__(
        self,
        common_formatter: CommonFormatter,
        policy_executor: PolicyExecutor,
        client_formatter: ClientFormatter,
        transaction_recorder: TransactionRecorder,
        queue_size: int = 10000,
    ) -> None:
        """Initialize orchestrator with injected dependencies.

        Args:
            common_formatter: Converts backend chunks to common format
            policy_executor: Executes policy logic on common-format chunks
            client_formatter: Converts common format to client SSE events
            transaction_recorder: Records chunks at pipeline boundaries
            queue_size: Maximum queue size (circuit breaker on overflow)
        """
        self.common_formatter = common_formatter
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
        pass  # TODO: Implement

    async def process_streaming_response(
        self,
        backend_stream: AsyncIterator[ModelResponse],
        policy_ctx: PolicyContext,
        obs_ctx: ObservabilityContext,
    ) -> AsyncIterator[str]:
        """Process streaming response through policy pipeline.

        This creates the explicit queue-based pipeline:
        1. backend_stream → CommonFormatter → common_in_queue
        2. common_in_queue → PolicyExecutor (recorded) → common_out_queue
        3. common_out_queue → ClientFormatter (recorded) → sse_queue
        4. Drain sse_queue and yield to client

        Args:
            backend_stream: Streaming ModelResponse from backend LLM
            policy_ctx: Policy context (shared with request processing)
            obs_ctx: Observability context for tracing

        Yields:
            SSE formatted strings in client-specific format

        Raises:
            PolicyTimeoutError: If policy processing times out
            QueueFullError: If any queue exceeds circuit breaker limit
            Exception: On pipeline errors
        """
        # Create typed queues that define pipeline contracts
        common_in_queue: asyncio.Queue[ModelResponse] = asyncio.Queue(maxsize=self.queue_size)
        common_out_queue: asyncio.Queue[ModelResponse] = asyncio.Queue(maxsize=self.queue_size)
        sse_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=self.queue_size)

        # Launch pipeline stages - structure is clear and typed
        asyncio.create_task(
            self.common_formatter.process(
                backend_stream,  # type: ignore  # Backend stream is input
                common_in_queue,
                policy_ctx,
                obs_ctx,
            )
        )
        asyncio.create_task(
            self.transaction_recorder.wrap(self.policy_executor).process(
                common_in_queue,
                common_out_queue,
                policy_ctx,
                obs_ctx,
            )
        )
        asyncio.create_task(
            self.transaction_recorder.wrap(self.client_formatter).process(
                common_out_queue,
                sse_queue,
                policy_ctx,
                obs_ctx,
            )
        )

        # Drain final queue and yield to client
        async for event in self._drain_queue(sse_queue):
            yield event

    async def _drain_queue(self, queue: asyncio.Queue[str]) -> AsyncIterator[str]:
        """Drain queue until shutdown.

        Args:
            queue: Queue to drain

        Yields:
            SSE strings from queue until QueueShutDown
        """
        pass  # TODO: Implement


class QueueFullError(Exception):
    """Raised when a pipeline queue exceeds circuit breaker limit."""

    pass


__all__ = ["PolicyOrchestrator", "QueueFullError"]
