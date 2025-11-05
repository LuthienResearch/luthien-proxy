# ABOUTME: PolicyExecutor implementation with keepalive-based timeout
# ABOUTME: Handles block assembly, policy hooks, and timeout monitoring

"""Policy executor implementation."""

import asyncio
import logging
import time
from typing import AsyncIterator

from litellm.types.utils import ModelResponse

from luthien_proxy.v2.observability.context import ObservabilityContext
from luthien_proxy.v2.policies.policy import PolicyProtocol
from luthien_proxy.v2.streaming.policy_executor.interface import (
    PolicyExecutorProtocol,
)
from luthien_proxy.v2.streaming.protocol import PolicyContext
from luthien_proxy.v2.streaming.stream_blocks import (
    ContentStreamBlock,
    ToolCallStreamBlock,
)
from luthien_proxy.v2.streaming.streaming_chunk_assembler import (
    StreamingChunkAssembler,
)
from luthien_proxy.v2.streaming.streaming_policy_context import StreamingPolicyContext

logger = logging.getLogger(__name__)

# Queue put timeout to prevent deadlock if downstream is slow
QUEUE_PUT_TIMEOUT = 30.0


class PolicyExecutor(PolicyExecutorProtocol):
    """Policy executor with keepalive-based timeout monitoring.

    Implements PolicyExecutorProtocol.

    This implementation:
    - Owns a BlockAssembler for building blocks from chunks
    - Invokes policy hooks as blocks are assembled
    - Enforces timeout unless keepalive() is called
    - Tracks last activity time internally

    Note: Policy is passed to process() method, not stored in executor.
    This makes the executor reusable with different policies.
    """

    def __init__(
        self,
        timeout_seconds: float | None = None,
    ) -> None:
        """Initialize policy executor.

        Args:
            timeout_seconds: Maximum time between keepalive calls before timeout.
                If None, no timeout is enforced.
        """
        self.timeout_seconds = timeout_seconds
        self._last_keepalive = time.monotonic()

    def keepalive(self) -> None:
        """Signal that policy is actively working, resetting timeout.

        Policies should call this during long-running operations to
        indicate they haven't stalled. Resets the internal activity
        timestamp used by timeout monitoring.
        """
        self._last_keepalive = time.monotonic()

    def _time_since_keepalive(self) -> float:
        """Time in seconds since last keepalive (or initialization).

        Used internally by timeout monitoring.

        Returns:
            Seconds since last keepalive() call or __init__
        """
        return time.monotonic() - self._last_keepalive

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
        )

        # Now set the real callback that uses the context
        assembler.on_chunk = self._create_chunk_callback(streaming_ctx, output_queue, policy)

        try:
            # Feed chunks to assembler - it will call our callback for each one
            await assembler.process(input_stream, context=streaming_ctx)

            # Call on_stream_complete after all chunks processed
            await policy.on_stream_complete(streaming_ctx)
        finally:
            # Signal end of stream with None sentinel
            await self._safe_put(output_queue, None)

    def _create_chunk_callback(
        self,
        streaming_ctx: StreamingPolicyContext,
        output_queue: asyncio.Queue[ModelResponse | None],
        policy: PolicyProtocol,
    ):
        """Create callback for assembler to invoke on each chunk.

        Args:
            streaming_ctx: Streaming policy context for hook invocations
            output_queue: Queue to write chunks to after policy processing
            policy: Policy instance implementing PolicyProtocol

        Returns:
            Async callback function
        """

        async def on_chunk(chunk: ModelResponse, state, context) -> None:
            """Called by assembler for each chunk after state update.

            Invokes policy hooks based on stream state, then drains egress queue.
            """
            self.keepalive()  # Update activity timestamp

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
            # If policy didn't write anything, forward the original chunk
            emitted_count = 0
            while not streaming_ctx.egress_queue.empty():
                try:
                    policy_chunk = streaming_ctx.egress_queue.get_nowait()
                    await self._safe_put(output_queue, policy_chunk)
                    emitted_count += 1
                except asyncio.QueueEmpty:
                    break

            # If policy didn't emit anything, forward original chunk
            if emitted_count == 0:
                await self._safe_put(output_queue, chunk)

        return on_chunk


__all__ = ["PolicyExecutor"]
