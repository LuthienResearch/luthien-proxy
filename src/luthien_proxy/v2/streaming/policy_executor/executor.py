# ABOUTME: PolicyExecutor implementation with keepalive-based timeout
# ABOUTME: Handles block assembly, policy hooks, and timeout monitoring

"""Policy executor implementation."""

import asyncio
import time
from typing import Any, AsyncIterator

from litellm.types.utils import ModelResponse

from luthien_proxy.v2.observability.context import ObservabilityContext
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


class PolicyExecutor:
    """Policy executor with keepalive-based timeout monitoring.

    Implements PolicyExecutorProtocol.

    This implementation:
    - Owns a BlockAssembler for building blocks from chunks
    - Invokes policy hooks as blocks are assembled
    - Enforces timeout unless keepalive() is called
    - Tracks last activity time internally
    """

    def __init__(
        self,
        policy: Any,  # BasePolicy or similar
        timeout_seconds: float | None = None,
    ) -> None:
        """Initialize policy executor.

        Args:
            policy: Policy instance with hook methods (on_chunk_added, etc.)
            timeout_seconds: Maximum time between keepalive calls before timeout.
                If None, no timeout is enforced.
        """
        self.policy = policy
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

    async def process(
        self,
        input_stream: AsyncIterator[ModelResponse],
        output_queue: asyncio.Queue[ModelResponse],
        policy_ctx: PolicyContext,
        obs_ctx: ObservabilityContext,
    ) -> None:
        """Execute policy processing on streaming chunks.

        This method:
        1. Reads chunks from input_stream
        2. Feeds them to BlockAssembler to build partial/complete blocks
        3. Invokes policy hooks at appropriate moments:
           - on_chunk_added: When a new chunk is added to a block
           - on_block_complete: When a block is fully assembled
           - on_tool_block_complete: When a tool use block completes
           - etc.
        4. Writes policy-approved chunks to output_queue
        5. Monitors for timeout (if configured), checking keepalive

        Args:
            input_stream: Stream of ModelResponse chunks from backend
            output_queue: Queue to write policy-approved chunks to
            policy_ctx: Policy context for shared state
            obs_ctx: Observability context for tracing

        Raises:
            PolicyTimeoutError: If processing exceeds timeout without keepalive
            Exception: On policy errors or assembly failures
        """
        # Step 3: Add policy hook invocations based on stream state
        # Define hook mappings based on block type
        DELTA_HOOKS = {
            ContentStreamBlock: self.policy.on_content_delta,
            ToolCallStreamBlock: self.policy.on_tool_call_delta,
        }
        COMPLETE_HOOKS = {
            ContentStreamBlock: self.policy.on_content_complete,
            ToolCallStreamBlock: self.policy.on_tool_call_complete,
        }

        # Create a minimal StreamingResponseContext for policy hooks
        # This will be replaced with proper context in Step 5
        egress_queue: asyncio.Queue[ModelResponse] = asyncio.Queue()
        streaming_ctx = StreamingResponseContext(
            transaction_id=policy_ctx.transaction_id,
            final_request=None,  # Will be added in Step 5
            ingress_assembler=None,  # Set below
            egress_queue=egress_queue,
            scratchpad=policy_ctx.scratchpad,
            observability=obs_ctx,
        )

        # Create callback that will be invoked by assembler on each chunk
        async def assembler_callback(chunk: ModelResponse, state: StreamState, context: Any) -> None:
            """Called by assembler for each chunk after state update.

            Invokes policy hooks based on stream state, then forwards chunk.
            """
            self.keepalive()  # Update activity timestamp

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

            # Forward chunk to output queue
            await output_queue.put(chunk)

        # Create assembler with our callback
        assembler = StreamingChunkAssembler(on_chunk_callback=assembler_callback)
        streaming_ctx.ingress_assembler = assembler  # Set assembler in context

        try:
            # Feed chunks to assembler - it will call our callback for each one
            await assembler.process(input_stream, context=streaming_ctx)

            # Call on_stream_complete after all chunks processed
            await self.policy.on_stream_complete(streaming_ctx)
        finally:
            # Signal end of stream with None sentinel
            await output_queue.put(None)

    async def _monitor_timeout(self, obs_ctx: ObservabilityContext) -> None:
        """Monitor policy execution time and raise on timeout.

        Runs as a background task, checking _time_since_keepalive()
        against the configured timeout. Raises PolicyTimeoutError if exceeded.

        Args:
            obs_ctx: Observability context for logging timeout

        Raises:
            PolicyTimeoutError: When timeout is exceeded
        """
        pass  # TODO: Implement


__all__ = ["PolicyExecutor"]
