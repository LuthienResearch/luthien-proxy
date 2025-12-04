# ABOUTME: PolicyExecutor implementation with timeout monitoring
# ABOUTME: Handles block assembly, policy hooks, and delegates timeout tracking to TimeoutMonitor

"""Policy executor implementation."""

import asyncio
import logging
from typing import AsyncIterator

from litellm.types.utils import ModelResponse
from opentelemetry import trace

from luthien_proxy.observability.context import ObservabilityContext
from luthien_proxy.observability.transaction_recorder import TransactionRecorder
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.policy_core.policy_protocol import PolicyProtocol
from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext
from luthien_proxy.streaming.policy_executor.interface import (
    PolicyExecutorProtocol,
    PolicyTimeoutError,
)
from luthien_proxy.streaming.policy_executor.timeout_monitor import TimeoutMonitor
from luthien_proxy.streaming.stream_blocks import (
    ContentStreamBlock,
    ToolCallStreamBlock,
)
from luthien_proxy.streaming.streaming_chunk_assembler import (
    StreamingChunkAssembler,
)

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

# Queue put timeout to prevent deadlock if downstream is slow
QUEUE_PUT_TIMEOUT = 30.0


class PolicyExecutor(PolicyExecutorProtocol):
    """Policy executor with keepalive-based timeout monitoring.

    Implements PolicyExecutorProtocol.

    This implementation:
    - Owns a BlockAssembler for building blocks from chunks
    - Invokes policy hooks as blocks are assembled
    - Enforces timeout using TimeoutMonitor
    - Delegates timeout tracking to TimeoutMonitor instance

    Note: Policy is passed to process() method, not stored in executor.
    This makes the executor reusable with different policies.
    """

    def __init__(
        self,
        recorder: TransactionRecorder,
        timeout_seconds: float | None = None,
    ) -> None:
        """Initialize policy executor.

        Args:
            timeout_seconds: Maximum time between keepalive calls before timeout.
                If None, no timeout is enforced.
            recorder: Transaction recorder for capturing ingress/egress chunks.
                If None, no recording is performed.
        """
        self.recorder = recorder
        self._timeout_monitor = TimeoutMonitor(timeout_seconds=timeout_seconds)

    def keepalive(self) -> None:
        """Signal that processing is actively working, resetting timeout.

        Policies can call this during long-running operations to indicate
        they haven't stalled. This is automatically called on each chunk,
        but policies doing expensive work between chunks may need to call
        it explicitly.
        """
        self._timeout_monitor.keepalive()

    async def _safe_put(self, queue: asyncio.Queue[ModelResponse | None], item: ModelResponse | None) -> None:
        """Safely put item in queue with timeout to prevent deadlock.

        Args:
            queue: Queue to put item into
            item: Item to put

        Raises:
            asyncio.TimeoutError: If queue is full and timeout is exceeded
        """
        try:
            await asyncio.wait_for(queue.put(item), timeout=QUEUE_PUT_TIMEOUT)
        except asyncio.TimeoutError:
            logger.error(f"Queue put timeout after {QUEUE_PUT_TIMEOUT}s - downstream may be slow or stalled")
            raise

    async def _cancel_task(self, task: asyncio.Task) -> None:
        """Cancel a task and wait for it to complete.

        Handles CancelledError gracefully. Safe to call on already-done tasks.

        Args:
            task: Task to cancel
        """
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def process(
        self,
        input_stream: AsyncIterator[ModelResponse],
        output_queue: asyncio.Queue[ModelResponse | None],
        policy: PolicyProtocol,
        policy_ctx: PolicyContext,
        obs_ctx: ObservabilityContext,
    ) -> None:
        """Execute policy processing on streaming chunks.

        This method:
        1. Reads chunks from input_stream
        2. Feeds them to BlockAssembler to build partial/complete blocks
        3. Invokes policy hooks at appropriate moments
        4. Writes policy-approved chunks to output_queue
        5. Monitors for timeout (if configured), checking keepalive

        Args:
            input_stream: Stream of ModelResponse chunks from backend
            output_queue: Queue to write policy-approved chunks to
            policy: Policy instance implementing PolicyProtocol (on_chunk_received, etc.)
            policy_ctx: Policy context for shared state
            obs_ctx: Observability context for tracing

        Raises:
            PolicyTimeoutError: If processing exceeds timeout without keepalive
            Exception: On policy errors or assembly failures
        """
        with tracer.start_as_current_span("streaming.policy_executor") as span:
            span.set_attribute("policy.class", policy.__class__.__name__)
            chunk_count = 0

            # Create egress queue for policies to write to
            egress_queue: asyncio.Queue[ModelResponse] = asyncio.Queue()

            # Create assembler - we'll pass the callback shortly
            # The assembler owns the state, so we create it first
            async def placeholder(*args):
                pass

            assembler = StreamingChunkAssembler(on_chunk_callback=placeholder)

            # Create streaming policy context
            streaming_ctx = StreamingPolicyContext(
                policy_ctx=policy_ctx,
                egress_queue=egress_queue,
                original_streaming_response_state=assembler.state,
                observability=obs_ctx,
                keepalive=self.keepalive,  # Pass executor's keepalive to policies
            )

            # Now set the real callback that uses the context
            chunk_callback, get_chunk_count = self._create_chunk_callback(streaming_ctx, output_queue, policy)
            assembler.on_chunk = chunk_callback

            # Define stream processing coroutine
            async def process_stream():
                nonlocal chunk_count
                # Feed chunks to assembler - it will call our callback for each one
                await assembler.process(input_stream, context=streaming_ctx)
                chunk_count = get_chunk_count()
                # Call on_stream_complete after all chunks processed
                await policy.on_stream_complete(streaming_ctx)

                # Drain any chunks added by on_stream_complete
                try:
                    while True:
                        policy_chunk = streaming_ctx.egress_queue.get_nowait()
                        self.recorder.add_egress_chunk(policy_chunk)
                        await self._safe_put(output_queue, policy_chunk)
                except asyncio.QueueEmpty:
                    pass

            # Create tasks for stream processing and timeout monitoring
            stream_task = asyncio.create_task(process_stream())
            monitor_task = asyncio.create_task(self._timeout_monitor.run())

            try:
                # Wait for either task to complete
                done, pending = await asyncio.wait({stream_task, monitor_task}, return_when=asyncio.FIRST_COMPLETED)

                # Check if monitor raised timeout
                if monitor_task in done:
                    # Monitor completed first - likely a timeout error
                    await self._cancel_task(stream_task)
                    span.set_attribute("streaming.timeout", True)
                    # Re-raise the timeout error from monitor (if any)
                    await monitor_task  # This will raise PolicyTimeoutError

                # Stream completed first - cancel monitor and get result
                await self._cancel_task(monitor_task)

                # Get result from stream task (may raise exception)
                await stream_task

                # Finalize recording (reconstruct and emit full responses)
                await self.recorder.finalize_streaming_response()

                span.set_attribute("streaming.chunk_count", chunk_count)
            except PolicyTimeoutError:
                # Timeout occurred - clean up and re-raise
                logger.debug("Policy timeout detected, cleaning up stream processing")
                raise
            except Exception:
                # Other error - ensure both tasks are cancelled
                await self._cancel_task(stream_task)
                await self._cancel_task(monitor_task)
                raise
            finally:
                # Call cleanup hook regardless of success/failure
                try:
                    await policy.on_streaming_policy_complete(streaming_ctx)
                except Exception:
                    logger.exception("Error in on_streaming_policy_complete - ignoring")

                # Ensure both tasks are cancelled if still running
                await self._cancel_task(stream_task)
                await self._cancel_task(monitor_task)
                # Signal end of stream with None sentinel
                await self._safe_put(output_queue, None)

    def _create_chunk_callback(
        self,
        streaming_ctx: StreamingPolicyContext,
        output_queue: asyncio.Queue[ModelResponse | None],
        policy: PolicyProtocol,
    ):
        """Create callback for assembler to invoke on each chunk.

        When each chunk arrives, callbacks (if they are called for this chunk) run in this order:
        1. on_chunk_received
        2. on_content_delta or on_tool_call_delta (if in a block)
        3. on_content_complete or on_tool_call_complete (if block just completed)
        4. on_finish_reason (if finish_reason is present)

        Args:
            streaming_ctx: Streaming policy context for hook invocations
            output_queue: Queue to write chunks to after policy processing
            policy: Policy instance implementing PolicyProtocol

        Returns:
            Tuple of (async callback function, chunk count getter)
        """
        ingress_chunk_count = 0

        def get_chunk_count() -> int:
            return ingress_chunk_count

        async def on_chunk(chunk: ModelResponse, state, context) -> None:
            """Called by assembler for each chunk after state update.

            Invokes policy hooks based on stream state, then drains egress queue.
            """
            nonlocal ingress_chunk_count
            self._timeout_monitor.keepalive()  # Update activity timestamp
            ingress_chunk_count += 1

            # Record ingress chunk (before policy processing)
            self.recorder.add_ingress_chunk(chunk)

            # Call on_chunk_received for every chunk
            await policy.on_chunk_received(streaming_ctx)

            # Call delta hooks if current block exists
            if state.current_block:
                if isinstance(state.current_block, ContentStreamBlock):
                    await policy.on_content_delta(streaming_ctx)
                elif isinstance(state.current_block, ToolCallStreamBlock):
                    await policy.on_tool_call_delta(streaming_ctx)

            # Call complete hooks if block just completed
            if state.just_completed:
                if isinstance(state.just_completed, ContentStreamBlock):
                    await policy.on_content_complete(streaming_ctx)
                elif isinstance(state.just_completed, ToolCallStreamBlock):
                    await policy.on_tool_call_complete(streaming_ctx)

            # Call finish_reason hook when present
            if state.finish_reason:
                await policy.on_finish_reason(streaming_ctx)

            # Drain egress queue (policy-approved chunks) to output
            while not streaming_ctx.egress_queue.empty():
                try:
                    policy_chunk = streaming_ctx.egress_queue.get_nowait()
                    # Record egress chunk (after policy processing)
                    self.recorder.add_egress_chunk(policy_chunk)
                    await self._safe_put(output_queue, policy_chunk)
                except asyncio.QueueEmpty:
                    break

        return on_chunk, get_chunk_count


__all__ = ["PolicyExecutor"]
