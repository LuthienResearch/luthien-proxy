"""Simplified policy orchestration with explicit streaming pipeline.

This refactored PolicyOrchestrator uses dependency injection
and explicit queues to create a clear, typed streaming pipeline.
"""

import asyncio
import logging
from typing import AsyncIterator

from litellm.types.utils import ModelResponse
from opentelemetry import trace

from luthien_proxy.llm.types import Request
from luthien_proxy.observability.transaction_recorder import TransactionRecorder
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.policy_core.policy_protocol import PolicyProtocol
from luthien_proxy.streaming.client_formatter import ClientFormatter
from luthien_proxy.streaming.policy_executor import PolicyExecutor
from luthien_proxy.utils.constants import DEFAULT_QUEUE_SIZE, LOG_SSE_EVENT_TRUNCATION_LENGTH

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


class PolicyOrchestrator:
    """Orchestrates request/response flow with explicit streaming pipeline.

    This orchestrator uses dependency injection to decouple pipeline stages:
    - Policy: Contains business logic for request/response transformation
    - PolicyExecutor: Block assembly + policy hook invocation (ModelResponse → ModelResponse)
    - ClientFormatter: Common format → client-specific SSE (ModelResponse → str)
    - TransactionRecorder: Records request/response data for observability

    The streaming pipeline is explicit, with typed queues connecting stages.
    Recording happens at natural boundaries within PolicyExecutor.

    Note: Backend streams from LiteLLM are already in common format (ModelResponse),
    so no ingress formatting is needed.
    """

    def __init__(
        self,
        policy: PolicyProtocol,
        policy_executor: PolicyExecutor,
        client_formatter: ClientFormatter,
        transaction_recorder: TransactionRecorder,
        queue_size: int = DEFAULT_QUEUE_SIZE,
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
    ) -> Request:
        """Apply policy to request before backend invocation.

        This processes the request through policy hooks before sending
        to the backend LLM. The policy_ctx is shared with streaming
        response processing.

        Args:
            request: Incoming request from client
            policy_ctx: Policy context (shared with response processing)

        Returns:
            Policy-modified request to send to backend

        Raises:
            PolicyError: If policy rejects the request
        """
        with tracer.start_as_current_span("policy.process_request") as span:
            span.set_attribute("policy.class", self.policy.__class__.__name__)
            span.set_attribute("request.model", request.model)
            span.set_attribute("request.message_count", len(request.messages))

            # Set request in context for policy access
            policy_ctx.request = request

            # Call policy's on_request hook
            final_request = await self.policy.on_request(request, policy_ctx)
            await self.transaction_recorder.record_request(request, final_request)

            span.set_attribute("request.modified", final_request != request)
            return final_request

    async def process_streaming_response(
        self,
        backend_stream: AsyncIterator[ModelResponse],
        policy_ctx: PolicyContext,
    ) -> AsyncIterator[str]:
        """Process streaming response through policy pipeline.

        This creates the explicit queue-based pipeline:
        1. backend_stream (ModelResponse) → PolicyExecutor → policy_out_queue
        2. policy_out_queue → ClientFormatter → sse_queue
        3. Drain sse_queue and yield to client

        Recording happens inside PolicyExecutor (ingress/egress chunks + finalization).

        Args:
            backend_stream: Streaming ModelResponse from backend LLM (already common format)
            policy_ctx: Policy context (shared with request processing)

        Yields:
            SSE formatted strings in client-specific format

        Raises:
            PolicyTimeoutError: If policy processing times out
            Exception: On pipeline errors (propagated from background tasks)
        """
        # Create typed queues that define pipeline contracts
        # Note: Queues use None as sentinel to signal end of stream
        policy_out_queue: asyncio.Queue[ModelResponse | None] = asyncio.Queue(maxsize=self.queue_size)
        sse_queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=self.queue_size)

        # Launch pipeline stages using TaskGroup to ensure error propagation
        # TaskGroup ensures that exceptions from background tasks are caught and raised
        async with asyncio.TaskGroup() as tg:
            # Start policy executor task
            tg.create_task(
                self.policy_executor.process(
                    backend_stream,
                    policy_out_queue,
                    self.policy,  # Pass policy to executor
                    policy_ctx,
                )
            )
            # Start client formatter task
            tg.create_task(
                self.client_formatter.process(
                    policy_out_queue,
                    sse_queue,
                    policy_ctx,
                )
            )

            # Drain final queue and yield to client while tasks run
            # If either task fails, TaskGroup will cancel remaining tasks and raise
            async for event in self._drain_queue(sse_queue):
                yield event

    async def process_full_response(
        self,
        response: ModelResponse,
        policy_ctx: PolicyContext,
    ) -> ModelResponse:
        """Process non-streaming full response through policy.

        Args:
            response: Full ModelResponse from backend LLM
            policy_ctx: Policy context (shared with request processing)
        """
        with tracer.start_as_current_span("policy.process_response") as span:
            span.set_attribute("policy.class", self.policy.__class__.__name__)

            # Call policy's on_response hook
            final_response = await self.policy.on_response(response, policy_ctx)
            await self.transaction_recorder.record_response(response, final_response)

            span.set_attribute("response.modified", final_response != response)
            return final_response

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
            logger.debug(f"[CLIENT OUT] {event[:LOG_SSE_EVENT_TRUNCATION_LENGTH]}")  # Truncate for readability
            yield event


__all__ = ["PolicyOrchestrator"]
