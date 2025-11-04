# ABOUTME: PolicyOrchestrator orchestrates request/response flow through policy layer
# ABOUTME: Handles both streaming and non-streaming responses with observability and recording

"""Module docstring."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Callable

from litellm.types.utils import ModelResponse
from opentelemetry import trace
from opentelemetry.trace import Span

from luthien_proxy.v2.llm.client import LLMClient
from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.observability.context import ObservabilityContext
from luthien_proxy.v2.observability.transaction_recorder import TransactionRecorder
from luthien_proxy.v2.policies.policy import Policy, PolicyContext
from luthien_proxy.v2.streaming.stream_blocks import (
    ContentStreamBlock,
    ToolCallStreamBlock,
)
from luthien_proxy.v2.streaming.stream_state import StreamState
from luthien_proxy.v2.streaming.streaming_chunk_assembler import (
    StreamingChunkAssembler,
)
from luthien_proxy.v2.streaming.streaming_orchestrator import StreamingOrchestrator
from luthien_proxy.v2.streaming.streaming_response_context import (
    StreamingResponseContext,
)

tracer = trace.get_tracer(__name__)


class PolicyOrchestrator:
    """Orchestrates request/response flow through policy layer."""

    def __init__(  # noqa: D107
        self,
        policy: Policy,
        llm_client: LLMClient,
        observability: ObservabilityContext,
        recorder: TransactionRecorder,
        streaming_orchestrator: StreamingOrchestrator | None = None,
    ):
        self.policy = policy
        self.llm_client = llm_client
        self.observability = observability
        self.recorder = recorder
        self.streaming_orchestrator = streaming_orchestrator or StreamingOrchestrator()

    async def process_request(self, request: Request, transaction_id: str, span: Span) -> Request:
        """Apply policy to request, record original + final."""
        context = PolicyContext(call_id=transaction_id, span=span, request=request, observability=self.observability)
        final_request = await self.policy.on_request(request, context)
        await self.recorder.record_request(request, final_request)

        return final_request

    async def _create_policy_callback(
        self,
        ctx: StreamingResponseContext,
        keepalive: Callable[[], None],
    ) -> Callable:
        """Create callback for assembler that invokes policy hooks.

        Returns a callback that processes chunks through policy hooks based on
        stream state (content deltas, tool calls, completion events).
        """
        DELTA_HOOKS = {
            ContentStreamBlock: self.policy.on_content_delta,
            ToolCallStreamBlock: self.policy.on_tool_call_delta,
        }
        COMPLETE_HOOKS = {
            ContentStreamBlock: self.policy.on_content_complete,
            ToolCallStreamBlock: self.policy.on_tool_call_complete,
        }

        async def policy_callback(chunk: ModelResponse, state: StreamState, context):
            """Called by assembler on each chunk."""
            keepalive()
            self.recorder.add_ingress_chunk(chunk)
            await self.policy.on_chunk_received(ctx)

            if state.current_block:
                block_type = type(state.current_block)
                if hook := DELTA_HOOKS.get(block_type):
                    await hook(ctx)

            if state.just_completed:
                block_type = type(state.just_completed)
                if hook := COMPLETE_HOOKS.get(block_type):
                    await hook(ctx)

            if state.finish_reason:
                await self.policy.on_finish_reason(ctx)

        return policy_callback

    @staticmethod
    async def _queue_to_async_iter(queue: asyncio.Queue) -> AsyncIterator:
        """Convert a queue to an async iterator.

        Yields items from the queue until QueueShutDown is raised.
        """
        while True:
            try:
                chunk = await queue.get()
                yield chunk
            except asyncio.QueueShutDown:
                break

    async def _feed_assembler(
        self,
        incoming_queue: asyncio.Queue,
        ingress_assembler: StreamingChunkAssembler,
        ctx: StreamingResponseContext,
        feed_complete: asyncio.Event,
    ):
        """Feed incoming chunks to assembler and notify policy on completion."""
        try:
            await ingress_assembler.process(self._queue_to_async_iter(incoming_queue), ctx)
            await self.policy.on_stream_complete(ctx)
        finally:
            feed_complete.set()

    async def _drain_egress(
        self,
        egress_queue: asyncio.Queue,
        outgoing_queue: asyncio.Queue,
        feed_complete: asyncio.Event,
        keepalive: Callable[[], None],
    ):
        """Drain egress queue and forward chunks to outgoing queue.

        Waits for chunks with timeout, and when feed is complete, drains any
        remaining chunks before shutting down the outgoing queue.
        """
        while True:
            try:
                chunk = await asyncio.wait_for(egress_queue.get(), timeout=0.1)
                self.recorder.add_egress_chunk(chunk)
                await outgoing_queue.put(chunk)
                keepalive()
            except asyncio.TimeoutError:
                if feed_complete.is_set():
                    # Drain remaining chunks
                    while not egress_queue.empty():
                        try:
                            chunk = egress_queue.get_nowait()
                            self.recorder.add_egress_chunk(chunk)
                            await outgoing_queue.put(chunk)
                            keepalive()
                        except asyncio.QueueEmpty:
                            break
                    break

        await outgoing_queue.put(None)
        outgoing_queue.shutdown()

    async def _policy_processor(
        self,
        incoming_queue: asyncio.Queue,
        outgoing_queue: asyncio.Queue,
        keepalive: Callable[[], None],
        ctx: StreamingResponseContext,
        egress_queue: asyncio.Queue,
        feed_complete: asyncio.Event,
    ):
        """Orchestrate policy processing of streaming chunks.

        Sets up assembler with policy callback, then runs feed and drain tasks
        concurrently.
        """
        policy_callback = await self._create_policy_callback(ctx, keepalive)
        ingress_assembler = StreamingChunkAssembler(on_chunk_callback=policy_callback)
        ctx.ingress_assembler = ingress_assembler

        await asyncio.gather(
            self._feed_assembler(incoming_queue, ingress_assembler, ctx, feed_complete),
            self._drain_egress(egress_queue, outgoing_queue, feed_complete, keepalive),
        )

    async def process_streaming_response(
        self, request: Request, transaction_id: str, span: Span
    ) -> AsyncIterator[ModelResponse]:
        """Process streaming response through policy."""
        llm_stream: AsyncIterator[ModelResponse] = await self.llm_client.stream(request)
        egress_queue: asyncio.Queue[ModelResponse] = asyncio.Queue()

        ctx = StreamingResponseContext(
            transaction_id=transaction_id,
            final_request=request,
            ingress_assembler=None,
            egress_queue=egress_queue,
            scratchpad={},
            observability=self.observability,
        )

        feed_complete = asyncio.Event()

        async def policy_processor(
            incoming_queue: asyncio.Queue,
            outgoing_queue: asyncio.Queue,
            keepalive: Callable[[], None],
        ):
            """Wrapper to call _policy_processor with captured state."""
            await self._policy_processor(incoming_queue, outgoing_queue, keepalive, ctx, egress_queue, feed_complete)

        try:
            async for chunk in self.streaming_orchestrator.process(
                llm_stream, policy_processor, timeout_seconds=30.0, span=span
            ):
                yield chunk
        finally:
            await self.recorder.finalize_streaming()

    async def process_full_response(self, request: Request, transaction_id: str, span: Span) -> ModelResponse:
        """Process non-streaming response through policy."""
        original_response = await self.llm_client.complete(request)

        context = PolicyContext(call_id=transaction_id, span=span, request=request, observability=self.observability)
        final_response = await self.policy.process_full_response(original_response, context)

        await self.recorder.finalize_non_streaming(original_response, final_response)

        return final_response


__all__ = ["PolicyOrchestrator"]
