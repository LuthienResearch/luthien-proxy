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
        observability_factory: Callable[[str, Span], ObservabilityContext],
        recorder_factory: Callable[[ObservabilityContext], TransactionRecorder],
        streaming_orchestrator: StreamingOrchestrator | None = None,
    ):
        self.policy = policy
        self.llm_client = llm_client
        self.observability_factory = observability_factory
        self.recorder_factory = recorder_factory
        self.streaming_orchestrator = streaming_orchestrator or StreamingOrchestrator()

    async def process_request(self, request: Request, transaction_id: str, span: Span) -> Request:
        """Apply policy to request, record original + final."""
        observability = self.observability_factory(transaction_id, span)
        recorder = self.recorder_factory(observability)

        context = PolicyContext(call_id=transaction_id, span=span, request=request, observability=observability)
        final_request = await self.policy.on_request(request, context)
        await recorder.record_request(request, final_request)

        return final_request

    async def process_streaming_response(
        self, request: Request, transaction_id: str, span: Span
    ) -> AsyncIterator[ModelResponse]:
        """Process streaming response through policy."""
        observability = self.observability_factory(transaction_id, span)
        recorder = self.recorder_factory(observability)

        llm_stream: AsyncIterator[ModelResponse] = await self.llm_client.stream(request)
        egress_queue: asyncio.Queue[ModelResponse] = asyncio.Queue()

        ctx = StreamingResponseContext(
            transaction_id=transaction_id,
            final_request=request,
            ingress_assembler=None,
            egress_queue=egress_queue,
            scratchpad={},
            observability=observability,
        )

        feed_complete = asyncio.Event()

        async def policy_processor(
            incoming_queue: asyncio.Queue,
            outgoing_queue: asyncio.Queue,
            keepalive: Callable[[], None],
        ):
            """Process chunks through policy."""
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
                recorder.add_ingress_chunk(chunk)
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

            ingress_assembler = StreamingChunkAssembler(on_chunk_callback=policy_callback)
            ctx.ingress_assembler = ingress_assembler

            async def feed_assembler():
                """Feed incoming chunks to assembler."""

                async def queue_to_iter():
                    while True:
                        try:
                            chunk = await incoming_queue.get()
                            if chunk is None:
                                break
                            yield chunk
                        except asyncio.QueueShutDown:
                            break

                try:
                    await ingress_assembler.process(queue_to_iter(), ctx)
                    await self.policy.on_stream_complete(ctx)
                finally:
                    feed_complete.set()

            async def drain_egress():
                """Drain egress queue and forward to outgoing."""
                while True:
                    try:
                        chunk = await asyncio.wait_for(egress_queue.get(), timeout=0.1)
                        recorder.add_egress_chunk(chunk)
                        await outgoing_queue.put(chunk)
                        keepalive()
                    except asyncio.TimeoutError:
                        if feed_complete.is_set():
                            while not egress_queue.empty():
                                try:
                                    chunk = egress_queue.get_nowait()
                                    recorder.add_egress_chunk(chunk)
                                    await outgoing_queue.put(chunk)
                                    keepalive()
                                except asyncio.QueueEmpty:
                                    break
                            break

                await outgoing_queue.put(None)
                outgoing_queue.shutdown()

            await asyncio.gather(feed_assembler(), drain_egress())

        try:
            async for chunk in self.streaming_orchestrator.process(
                llm_stream, policy_processor, timeout_seconds=30.0, span=span
            ):
                yield chunk
        finally:
            await recorder.finalize_streaming()

    async def process_full_response(self, request: Request, transaction_id: str, span: Span) -> ModelResponse:
        """Process non-streaming response through policy."""
        observability = self.observability_factory(transaction_id, span)
        recorder = self.recorder_factory(observability)

        original_response = await self.llm_client.complete(request)

        context = PolicyContext(call_id=transaction_id, span=span, request=request, observability=observability)
        final_response = await self.policy.process_full_response(original_response, context)

        await recorder.finalize_non_streaming(original_response, final_response)

        return final_response


__all__ = ["PolicyOrchestrator"]
