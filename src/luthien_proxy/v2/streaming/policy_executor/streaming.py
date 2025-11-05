# ABOUTME: StreamingPolicyExecutor - simple policy execution for streaming responses
# ABOUTME: Wires assembler → policy hooks → egress queue with timeout monitoring

"""Streaming policy executor implementation."""

import asyncio
import time
from typing import Any, AsyncIterator

from litellm.types.utils import ModelResponse

from luthien_proxy.v2.observability.context import ObservabilityContext
from luthien_proxy.v2.policies.policy import Policy
from luthien_proxy.v2.streaming.policy_executor.interface import (
    PolicyExecutor,
    PolicyTimeoutError,
)
from luthien_proxy.v2.streaming.protocol import PolicyContext
from luthien_proxy.v2.streaming.stream_blocks import (
    ContentStreamBlock,
    ToolCallStreamBlock,
)
from luthien_proxy.v2.streaming.stream_state import StreamState
from luthien_proxy.v2.streaming.streaming_chunk_assembler import (
    StreamingChunkAssembler,
)
from luthien_proxy.v2.streaming.streaming_response_context import (
    StreamingResponseContext,
)


class StreamingPolicyExecutor(PolicyExecutor):
    """Executes policy hooks during streaming response processing.

    Simple design:
    1. Create egress_queue for policy to write modified chunks
    2. Create StreamingResponseContext with egress_queue
    3. Wire assembler callback to invoke policy hooks
    4. Feed input_stream → assembler → policy hooks → egress_queue
    5. Drain egress_queue → output_queue
    6. Monitor timeout in background task
    """

    def __init__(
        self,
        policy: Policy,
        timeout_seconds: float | None = None,
    ) -> None:
        """Initialize streaming policy executor.

        Args:
            policy: Policy instance with hook methods
            timeout_seconds: Maximum time between keepalive calls before timeout.
                If None, no timeout is enforced.
        """
        self.policy = policy
        self.timeout_seconds = timeout_seconds
        self._last_keepalive = time.monotonic()

    def keepalive(self) -> None:
        """Signal that policy is actively working, resetting timeout."""
        self._last_keepalive = time.monotonic()

    def _time_since_keepalive(self) -> float:
        """Time in seconds since last keepalive."""
        return time.monotonic() - self._last_keepalive

    async def process(
        self,
        input_stream: AsyncIterator[ModelResponse],
        output_queue: asyncio.Queue[ModelResponse],
        policy_ctx: PolicyContext,
        obs_ctx: ObservabilityContext,
    ) -> None:
        """Execute policy processing on streaming chunks.

        Creates StreamingResponseContext with egress_queue, feeds chunks through
        assembler which invokes policy hooks, then drains egress to output.

        Args:
            input_stream: Stream of ModelResponse chunks from backend
            output_queue: Queue to write policy-approved chunks to
            policy_ctx: Policy context for shared state
            obs_ctx: Observability context for tracing

        Raises:
            PolicyTimeoutError: If processing exceeds timeout without keepalive
        """
        # Create egress queue for policy to write modified chunks
        egress_queue: asyncio.Queue[ModelResponse] = asyncio.Queue()

        # Create StreamingResponseContext that policy hooks receive
        streaming_ctx = StreamingResponseContext(
            transaction_id=policy_ctx.transaction_id,
            final_request=None,  # Will be set by orchestrator if needed
            ingress_assembler=None,  # Set below
            egress_queue=egress_queue,
            scratchpad=policy_ctx.scratchpad,
            observability=obs_ctx,
        )

        # Define hook mappings based on block type
        DELTA_HOOKS = {
            ContentStreamBlock: self.policy.on_content_delta,
            ToolCallStreamBlock: self.policy.on_tool_call_delta,
        }
        COMPLETE_HOOKS = {
            ContentStreamBlock: self.policy.on_content_complete,
            ToolCallStreamBlock: self.policy.on_tool_call_complete,
        }

        # Create assembler callback that invokes policy hooks
        async def assembler_callback(chunk: ModelResponse, state: StreamState, context: Any) -> None:
            """Invoked by assembler for each chunk - calls policy hooks."""
            self.keepalive()

            # Call on_chunk_received for every chunk
            await self.policy.on_chunk_received(streaming_ctx)

            # Call delta hook if current block exists
            if state.current_block:
                block_type = type(state.current_block)
                if hook := DELTA_HOOKS.get(block_type):
                    await hook(streaming_ctx)

            # Call complete hook if block just completed
            if state.just_completed:
                block_type = type(state.just_completed)
                if hook := COMPLETE_HOOKS.get(block_type):
                    await hook(streaming_ctx)

            # Call finish_reason hook when present
            if state.finish_reason:
                await self.policy.on_finish_reason(streaming_ctx)

            # Forward chunk to egress queue (policy may have modified it)
            await egress_queue.put(chunk)

        # Create assembler with callback
        assembler = StreamingChunkAssembler(on_chunk_callback=assembler_callback)
        streaming_ctx.ingress_assembler = assembler

        # Track when feeding is complete
        feed_complete = asyncio.Event()

        async def feed_assembler():
            """Feed chunks from input_stream to assembler."""
            try:
                await assembler.process(input_stream, context=streaming_ctx)
                await self.policy.on_stream_complete(streaming_ctx)
            finally:
                feed_complete.set()

        async def drain_egress():
            """Drain egress_queue to output_queue."""
            while True:
                try:
                    # Wait with small timeout so we can check feed_complete
                    chunk = await asyncio.wait_for(egress_queue.get(), timeout=0.1)
                    await output_queue.put(chunk)
                    self.keepalive()
                except asyncio.TimeoutError:
                    if feed_complete.is_set():
                        # Feed done, drain any remaining chunks
                        while not egress_queue.empty():
                            chunk = egress_queue.get_nowait()
                            await output_queue.put(chunk)
                        break

        async def monitor_timeout():
            """Monitor for timeout if configured."""
            if self.timeout_seconds is None:
                return  # No timeout monitoring

            while not feed_complete.is_set():
                await asyncio.sleep(0.1)
                if self._time_since_keepalive() > self.timeout_seconds:
                    raise PolicyTimeoutError(f"Policy execution exceeded {self.timeout_seconds}s timeout")

        try:
            # Run all tasks concurrently
            await asyncio.gather(
                feed_assembler(),
                drain_egress(),
                monitor_timeout(),
            )
        finally:
            # Signal end of stream
            await output_queue.put(None)


__all__ = ["StreamingPolicyExecutor"]
