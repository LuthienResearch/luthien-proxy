# ABOUTME: V3 Event-based policy base class with block-level hooks
# ABOUTME: Provides high-level streaming policy DSL using StreamProcessor

"""Event-based policy DSL for V3 architecture.

This module provides a block-level abstraction for writing streaming policies.
Instead of working with raw chunks, policies receive callbacks for complete
blocks (content, tool calls) and can easily transform, judge, or filter them.

Key concepts:
- EventBasedPolicy: Base class inheriting from LuthienPolicy
- Block-level hooks: on_content_delta, on_content_complete, on_tool_call_complete
- StreamingContext: Safe interface for sending chunks and checking output state
- Automatic forwarding: Default implementations forward content automatically

Example:
    class NoOpPolicy(EventBasedPolicy):
        # No overrides needed - defaults forward everything!
        pass
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any, Callable, cast

from litellm.types.utils import ModelResponse, StreamingChoices

from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.policies.base import LuthienPolicy
from luthien_proxy.v2.policies.policy_context import PolicyContext
from luthien_proxy.v2.streaming.stream_blocks import (
    ContentStreamBlock,
    ToolCallStreamBlock,
)
from luthien_proxy.v2.streaming.stream_processor import StreamProcessor
from luthien_proxy.v2.streaming.stream_state import StreamState

logger = logging.getLogger(__name__)


class StreamingContext:
    """Per-request context for streaming policy hooks.

    Provides safe, controlled access to stream operations.
    Hooks can send chunks but cannot directly access queues.

    Policies continue processing the incoming stream even after finishing
    output. This allows observability, metrics, and cleanup to occur
    for the entire stream lifecycle.
    """

    def __init__(
        self,
        policy_context: PolicyContext,
        keepalive: Callable[[], None] | None,
        outgoing: asyncio.Queue[ModelResponse],
    ):
        """Initialize streaming context.

        Args:
            policy_context: PolicyContext with request, scratchpad, emit(), etc.
            keepalive: Optional callback to prevent timeout during slow operations
            outgoing: Queue for sending chunks to client
        """
        self.policy_context = policy_context
        self.keepalive = keepalive
        self._outgoing = outgoing
        self._output_finished = False

    async def send(self, chunk: ModelResponse) -> None:
        """Send a raw chunk to client.

        Raises:
            RuntimeError: If called after output stream is finished
        """
        if self._output_finished:
            raise RuntimeError("Cannot send chunks after output stream is finished")
        await self._outgoing.put(chunk)

    async def send_text(self, text: str, finish: bool = False) -> None:
        """Convenience: send text as a chunk.

        Args:
            text: Text content to send
            finish: If True, mark output stream as finished
        """
        from luthien_proxy.v2.streaming.utils import build_text_chunk

        chunk = build_text_chunk(
            text,
            model=self.policy_context.request.model,
            finish_reason="stop" if finish else None,
        )
        await self.send(chunk)
        if finish:
            self._output_finished = True

    def mark_output_finished(self) -> None:
        """Mark output stream as finished (no more sends allowed).

        Use this when you've sent a final chunk and want to prevent
        further output, but continue processing input for observability.
        """
        self._output_finished = True

    def is_output_finished(self) -> bool:
        """Check if output stream is finished.

        Returns:
            True if output stream has been marked finished
        """
        return self._output_finished


class EventBasedPolicy(LuthienPolicy):
    """Base class for event-based policies (V3 architecture).

    Policies are stateless - they define behavior.
    Per-request state lives in PolicyContext.scratchpad.

    Provides hooks for all stages:
    - Request: on_request()
    - Non-streaming response: on_response()
    - Streaming response: on_stream_* hooks

    Default implementations pass data through unchanged.
    Override only the hooks you need.
    """

    # ------------------------------------------------------------------
    # Request events
    # ------------------------------------------------------------------

    async def on_request(
        self,
        request: Request,
        context: PolicyContext,
    ) -> Request:
        """Process request before sending to LLM.

        Default: return unchanged.

        Args:
            request: Request to process
            context: Per-request context for events and state

        Returns:
            Modified request (or raise to reject)
        """
        return request

    # ------------------------------------------------------------------
    # Non-streaming response events
    # ------------------------------------------------------------------

    async def on_response(
        self,
        response: ModelResponse,
        context: PolicyContext,
    ) -> ModelResponse:
        """Process complete (non-streaming) response.

        Default: return unchanged.

        Args:
            response: Complete response from LLM
            context: Per-request context (includes original request)

        Returns:
            Modified response
        """
        return response

    # ------------------------------------------------------------------
    # Streaming response events
    # ------------------------------------------------------------------

    async def on_stream_start(
        self,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> None:
        """Called before first chunk is processed.

        Use for initialization, setup, etc.

        Args:
            context: Per-request context (call_id, span, emit)
            streaming_ctx: Streaming context (send, keepalive)
        """
        pass

    async def on_content_delta(
        self,
        delta: str,
        block: ContentStreamBlock,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> None:
        """Called for each content text delta.

        Default: forward delta to client immediately.

        Args:
            delta: Text delta from this chunk
            block: Content block with aggregated content so far
            context: Per-request context
            streaming_ctx: Streaming context
        """
        await streaming_ctx.send_text(delta)

    async def on_content_complete(
        self,
        block: ContentStreamBlock,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> None:
        """Called when content block completes.

        Default: no-op (deltas already forwarded).

        Use this hook for:
        - Metrics/logging after content block finishes
        - Transformations that need the complete content
        - Validations on full content text

        Args:
            block: Completed content block (block.content has full text)
            context: Per-request context
            streaming_ctx: Streaming context
        """
        pass

    async def on_tool_call_delta(
        self,
        raw_chunk: ModelResponse,
        block: ToolCallStreamBlock,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> None:
        """Called for each tool call delta chunk.

        Default: forward raw chunk to client.

        Override this to prevent forwarding tool call deltas until
        the tool call is complete and evaluated (e.g., for judging).

        Args:
            raw_chunk: Raw chunk with tool call delta
            block: Tool call block with aggregated data so far
            context: Per-request context
            streaming_ctx: Streaming context
        """
        await streaming_ctx.send(raw_chunk)

    async def on_tool_call_complete(
        self,
        block: ToolCallStreamBlock,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> None:
        """Called when a tool call block completes.

        Default: no-op (deltas already forwarded).

        Use this hook for:
        - Judging/validating complete tool calls
        - Converting block to chunk with build_block_chunk()
        - Metrics/logging after tool call finishes

        If you overrode on_tool_call_delta() to prevent forwarding,
        you must forward the block here (if it passes validation):
            chunk = build_block_chunk(block, model=context.request.model)
            await streaming_ctx.send(chunk)

        Args:
            block: Completed tool call (block.name and block.arguments available)
            context: Per-request context
            streaming_ctx: Streaming context
        """
        pass

    async def on_finish_reason(
        self,
        finish_reason: str,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> None:
        """Called when finish_reason is received.

        Default: forward finish reason chunk.

        Since on_content_delta only forwards content (not finish_reason),
        we need to send a finish chunk here.

        Override this if you need custom finish handling, e.g.:
        - Policies that buffer content should send finish chunk differently
        - Policies that block output should prevent finish chunk here

        Args:
            finish_reason: "stop", "tool_calls", "length", etc.
            context: Per-request context
            streaming_ctx: Streaming context
        """
        # Send a finish-only chunk
        from luthien_proxy.v2.streaming.utils import build_text_chunk

        finish_chunk = build_text_chunk(
            "",  # No content, just finish_reason
            model=context.request.model,
            finish_reason=finish_reason,
        )
        await streaming_ctx.send(finish_chunk)

    async def on_stream_complete(
        self,
        context: PolicyContext,
    ) -> None:
        """Always called in finally block after stream ends.

        Use for cleanup, final events, etc.

        Args:
            context: Per-request context
        """
        pass

    # ------------------------------------------------------------------
    # LuthienPolicy interface implementation
    # ------------------------------------------------------------------

    async def process_request(
        self,
        request: Request,
        context: PolicyContext,
    ) -> Request:
        """Process request - delegates to on_request()."""
        return await self.on_request(request, context)

    async def process_full_response(
        self,
        response: ModelResponse,
        context: PolicyContext,
    ) -> ModelResponse:
        """Process non-streaming response - delegates to on_response()."""
        return await self.on_response(response, context)

    async def process_streaming_response(
        self,
        incoming: asyncio.Queue[ModelResponse],
        outgoing: asyncio.Queue[ModelResponse],
        context: PolicyContext,
        keepalive: Callable[[], None] | None = None,
    ) -> None:
        """Process streaming response using StreamProcessor and hooks.

        Creates StreamProcessor with callback that dispatches to hooks.
        Manages StreamingContext and lifecycle.

        This is called by StreamingOrchestrator via SynchronousControlPlane.
        """
        # Create streaming context for hooks
        streaming_ctx = StreamingContext(
            policy_context=context,
            keepalive=keepalive,
            outgoing=outgoing,
        )

        try:
            # Call stream start hook
            await self.on_stream_start(context, streaming_ctx)

            # Create processor with callback that dispatches to hooks
            async def on_chunk_callback(
                chunk: ModelResponse,
                state: StreamState,
                ctx: Any,  # This is streaming_ctx passed to processor.process()
            ) -> None:
                """Dispatcher from StreamProcessor to policy hooks."""
                # Content delta?
                if state.current_block and isinstance(state.current_block, ContentStreamBlock):
                    delta = self._extract_content_delta(chunk)
                    if delta:
                        await self.on_content_delta(delta, state.current_block, context, streaming_ctx)

                # Content complete?
                if state.just_completed and isinstance(state.just_completed, ContentStreamBlock):
                    await self.on_content_complete(state.just_completed, context, streaming_ctx)

                # Tool call delta?
                if state.current_block and isinstance(state.current_block, ToolCallStreamBlock):
                    await self.on_tool_call_delta(chunk, state.current_block, context, streaming_ctx)

                # Tool call complete?
                if state.just_completed and isinstance(state.just_completed, ToolCallStreamBlock):
                    await self.on_tool_call_complete(state.just_completed, context, streaming_ctx)

                # Finish reason?
                if state.finish_reason:
                    await self.on_finish_reason(state.finish_reason, context, streaming_ctx)

            # Create processor and run
            processor = StreamProcessor(on_chunk_callback=on_chunk_callback)

            # Convert queue to async iterator
            async def queue_to_iter() -> AsyncIterator[ModelResponse]:
                while True:
                    try:
                        chunk = await incoming.get()
                        yield chunk
                    except asyncio.QueueShutDown:
                        break

            await processor.process(queue_to_iter(), streaming_ctx)

        finally:
            # Always call complete hook and shutdown queue
            await self.on_stream_complete(context)
            outgoing.shutdown()

    def _extract_content_delta(self, chunk: ModelResponse) -> str | None:
        """Extract content delta from chunk, if present."""
        if not chunk.choices:
            return None
        choice = cast(StreamingChoices, chunk.choices[0])
        delta = choice.delta
        # Handle both dict and Delta object
        if isinstance(delta, dict):
            return delta.get("content")
        elif hasattr(delta, "content"):
            return delta.content  # type: ignore[union-attr]
        return None


__all__ = [
    "EventBasedPolicy",
    "StreamingContext",
]
