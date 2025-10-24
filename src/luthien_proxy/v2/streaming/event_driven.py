# ABOUTME: Event-driven policy DSL base class and supporting types
# ABOUTME: Provides hook-based streaming policy authoring with safe lifecycle management

"""Event-driven policy DSL for streaming policies.

This module provides a safe, hook-based abstraction for writing streaming policies.
Instead of manually managing queues, lifecycle, and chunk parsing, policies override
hooks that fire at specific points in the stream lifecycle.

Key concepts:
- EventDrivenPolicy: Base class with canonical hook methods
- StreamingContext: Per-request context passed to all hooks
- TerminateStream: Exception for graceful stream termination
- Hooks have direct write access via context.send()
- Incoming and outgoing streams are fully decoupled

Example:
    class NoOpPolicy(EventDrivenPolicy):
        def create_state(self):
            return None

        async def on_chunk_complete(self, raw_chunk, state, context):
            await context.send(raw_chunk)
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable

from litellm.types.utils import ModelResponse

from luthien_proxy.v2.policies.context import PolicyContext

logger = logging.getLogger(__name__)


class TerminateStream(Exception):
    """Exception for graceful stream termination.

    When raised from a hook, the base class treats this as a graceful termination:
    - Stops processing after the current hook
    - Skips remaining per-chunk hooks
    - Skips on_stream_error
    - Runs on_stream_closed
    - Performs pump shutdown

    The exception message is logged but not re-raised.
    """

    pass


@dataclass
class StreamingContext:
    """Per-request context for streaming policy hooks.

    Provides safe, controlled access to stream operations.
    Hooks can send chunks but cannot directly access queues.

    Policies continue processing the incoming stream even after finishing
    output. This allows observability, metrics, and cleanup to occur
    for the entire stream lifecycle.

    Attributes:
        policy_context: PolicyContext with request, scratchpad, emit(), etc.
        keepalive: Optional callback to invoke during long operations (e.g., judge calls)
                   to prevent upstream timeout. Call periodically if hook blocks >1s.
    """

    policy_context: PolicyContext
    keepalive: Callable[[], None] | None = None
    _outgoing: asyncio.Queue[ModelResponse] | None = None  # Set by base class
    _output_finished: bool = False  # V3: Flag indicating output stream is complete
    _terminate_flag: bool = False  # Internal flag set by terminate()

    async def send(self, chunk: ModelResponse) -> None:
        """Send a raw chunk to client.

        Raises:
            RuntimeError: If called after output stream is finished or queue not initialized
        """
        if self._output_finished:
            raise RuntimeError("Cannot send chunks after output stream is finished")

        if self._outgoing is None:
            raise RuntimeError("StreamingContext not properly initialized - no outgoing queue")

        await self._outgoing.put(chunk)

    async def send_text(self, text: str, finish: bool = False) -> None:
        """Convenience: send text as a chunk.

        Args:
            text: Text content to send
            finish: If True, mark output stream as finished

        Raises:
            RuntimeError: If model not available in context or if output already finished
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

    def emit(self, event_type: str, summary: str, **kwargs: Any) -> None:
        """Emit a policy event for logging/metrics.

        Args:
            event_type: Event type identifier (e.g., "dsl.hook_called")
            summary: Human-readable event summary
            **kwargs: Additional event data (severity, details, etc.)
        """
        self.policy_context.emit(event_type, summary, **kwargs)

    def terminate(self) -> None:
        """Request graceful stream termination.

        Sets internal flag; the base class stops processing after the
        current hook completes, skips downstream hooks, and shuts down.
        Also marks output as finished to prevent further sends.
        """
        self._terminate_flag = True
        self._output_finished = True


class EventDrivenPolicy(ABC):
    """Base class for hook-based streaming policies.

    This class implements the queue consumption loop and chunk parsing, exposing
    a canonical sequence of lifecycle hooks that subclasses override.

    Hooks are called in a fixed, predictable order for each chunk.
    Hooks return None and call context.send() to emit chunks.
    Default implementations are no-ops, so subclasses opt-in only where needed.

    Hook lifecycle (per chunk):
        1. on_chunk_started(raw_chunk, state, context)
        2. on_role_delta(role, raw_chunk, state, context) - if delta.role present
        3. on_content_chunk(content, raw_chunk, state, context) - if delta.content present
        4. on_tool_call_delta(delta, raw_chunk, state, context) - for each delta.tool_calls[i]
        5. on_usage_delta(usage, raw_chunk, state, context) - if usage present
        6. on_finish_reason(reason, raw_chunk, state, context) - if finish_reason present
        7. on_chunk_complete(raw_chunk, state, context)

    Stream-level hooks:
        - on_stream_started(state, context) - before first chunk
        - on_stream_closed(state, context) - after last chunk (always called)
        - on_stream_error(error, state, context) - on unexpected exceptions

    API guarantees:
        ✅ Hooks can: await context.send(), context.keepalive(), context.terminate(), raise TerminateStream
        ❌ Hooks cannot: access queues, call shutdown(), break lifecycle

    Example:
        class ContentOnlyPolicy(EventDrivenPolicy):
            def create_state(self):
                return None

            async def on_content_chunk(self, content, raw_chunk, state, context):
                await context.send(raw_chunk)
    """

    @abstractmethod
    def create_state(self) -> Any:
        """Create per-request state object.

        Called once per request before on_stream_started.
        Return any object (dataclass, SimpleNamespace, dict, etc.) to hold
        mutable state across hook invocations.

        State is passed to every hook as the 'state' parameter.
        Do NOT store state on self - policy instances are shared across requests.

        Returns:
            State object for this request
        """
        ...

    # ------------------------------------------------------------------
    # Stream-level hooks
    # ------------------------------------------------------------------

    async def on_stream_started(self, state: Any, context: StreamingContext) -> None:
        """Called before first chunk is processed.

        Args:
            state: Per-request state from create_state()
            context: Streaming context for this request
        """
        pass

    async def on_stream_closed(self, state: Any, context: StreamingContext) -> None:
        """Called after last chunk, always runs (even on errors).

        This is the place to flush buffered chunks, emit final events, etc.
        Always called exactly once, in a finally block.

        Args:
            state: Per-request state
            context: Streaming context
        """
        pass

    async def on_stream_error(self, error: Exception, state: Any, context: StreamingContext) -> None:
        """Called when unexpected exception occurs (not TerminateStream).

        Args:
            error: Exception that occurred
            state: Per-request state
            context: Streaming context
        """
        pass

    # ------------------------------------------------------------------
    # Per-chunk hooks (canonical order)
    # ------------------------------------------------------------------

    async def on_chunk_started(self, raw_chunk: ModelResponse, state: Any, context: StreamingContext) -> None:
        """Called when chunk is received from queue (before parsing).

        Args:
            raw_chunk: Raw ModelResponse chunk
            state: Per-request state
            context: Streaming context
        """
        pass

    async def on_role_delta(self, role: str, raw_chunk: ModelResponse, state: Any, context: StreamingContext) -> None:
        """Called when delta.role is present.

        Args:
            role: Role string (e.g., "assistant")
            raw_chunk: Raw ModelResponse chunk
            state: Per-request state
            context: Streaming context
        """
        pass

    async def on_content_chunk(
        self, content: str, raw_chunk: ModelResponse, state: Any, context: StreamingContext
    ) -> None:
        """Called when delta.content is present.

        Args:
            content: Text content delta
            raw_chunk: Raw ModelResponse chunk
            state: Per-request state
            context: Streaming context
        """
        pass

    async def on_tool_call_delta(
        self, delta: dict[str, Any], raw_chunk: ModelResponse, state: Any, context: StreamingContext
    ) -> None:
        """Called for each delta.tool_calls[i] in the chunk.

        Args:
            delta: Tool call delta dict (index, id, type, function)
            raw_chunk: Raw ModelResponse chunk
            state: Per-request state
            context: Streaming context
        """
        pass

    async def on_usage_delta(
        self, usage: dict[str, Any], raw_chunk: ModelResponse, state: Any, context: StreamingContext
    ) -> None:
        """Called when usage field is present.

        Args:
            usage: Usage dict (prompt_tokens, completion_tokens, etc.)
            raw_chunk: Raw ModelResponse chunk
            state: Per-request state
            context: Streaming context
        """
        pass

    async def on_finish_reason(
        self, reason: str, raw_chunk: ModelResponse, state: Any, context: StreamingContext
    ) -> None:
        """Called when finish_reason is present.

        Args:
            reason: Finish reason string ("stop", "tool_calls", "length", etc.)
            raw_chunk: Raw ModelResponse chunk
            state: Per-request state
            context: Streaming context
        """
        pass

    async def on_chunk_complete(self, raw_chunk: ModelResponse, state: Any, context: StreamingContext) -> None:
        """Called after all delta hooks for this chunk.

        Args:
            raw_chunk: Raw ModelResponse chunk
            state: Per-request state
            context: Streaming context
        """
        pass

    # ------------------------------------------------------------------
    # Main processing loop (called by LuthienPolicy integration)
    # ------------------------------------------------------------------

    async def process_streaming_response(
        self,
        incoming: asyncio.Queue[ModelResponse],
        outgoing: asyncio.Queue[ModelResponse],
        context: PolicyContext,
        keepalive: Callable[[], None] | None = None,
    ) -> None:
        """Process streaming response using hooks.

        This method implements the queue consumption loop and calls hooks
        in canonical order. Subclasses should NOT override this method.

        Args:
            incoming: Queue of chunks from LLM (shut down when stream ends)
            outgoing: Queue of chunks to send to client
            context: Policy context for event emission
            keepalive: Optional callback to prevent timeout
        """
        # Create per-request state
        state = self.create_state()

        # V3: Create streaming context with PolicyContext (includes request)
        streaming_context = StreamingContext(
            policy_context=context,
            keepalive=keepalive,
            _outgoing=outgoing,
            _output_finished=False,
        )

        # Track if any chunks were emitted
        chunks_emitted = False

        try:
            # Stream-level hook: started
            await self.on_stream_started(state, streaming_context)

            # Process chunks until stream ends
            while True:
                # Check termination flag
                if streaming_context._terminate_flag:
                    context.emit("dsl.terminated", "Stream terminated by policy", severity="info")
                    break

                # Get next chunk
                try:
                    chunk = await incoming.get()
                except asyncio.QueueShutDown:
                    # Stream ended normally
                    break

                # Track queue size before processing
                pre_size = outgoing.qsize()

                # Process chunk through hooks
                try:
                    await self._process_chunk(chunk, state, streaming_context)
                except TerminateStream as exc:
                    # Graceful termination via exception
                    context.emit(
                        "dsl.terminated_exception",
                        f"Stream terminated via TerminateStream: {exc}",
                        severity="info",
                    )
                    break

                # Check if chunk was emitted
                post_size = outgoing.qsize()
                if post_size > pre_size:
                    chunks_emitted = True

                # Check termination flag again
                if streaming_context._terminate_flag:
                    context.emit("dsl.terminated", "Stream terminated by policy", severity="info")
                    break

        except Exception as error:
            # Unexpected error - call error hook
            try:
                await self.on_stream_error(error, state, streaming_context)
            except Exception as hook_error:
                logger.error(f"on_stream_error raised exception: {hook_error}", exc_info=True)

            # Log both exceptions
            logger.error(f"Stream processing error: {error}", exc_info=True)

            # Re-raise original error
            raise

        finally:
            # Always call closed hook
            try:
                await self.on_stream_closed(state, streaming_context)
            except Exception as exc:
                logger.error(f"on_stream_closed raised exception: {exc}", exc_info=True)

            # Shutdown outgoing queue
            outgoing.shutdown()

            # Warn if no chunks were emitted
            if not chunks_emitted:
                context.emit(
                    "dsl.no_output",
                    "Stream ended without emitting any chunks",
                    severity="warning",
                )

    async def _process_chunk(self, chunk: ModelResponse, state: Any, context: StreamingContext) -> None:
        """Process a single chunk through the hook lifecycle.

        Args:
            chunk: Raw ModelResponse chunk
            state: Per-request state
            context: Streaming context
        """
        # Hook: chunk started
        await self.on_chunk_started(chunk, state, context)
        if context._terminate_flag:
            return

        # Parse chunk to dict
        chunk_dict = chunk.model_dump() if hasattr(chunk, "model_dump") else dict(chunk)  # type: ignore

        # Extract choices
        choices = chunk_dict.get("choices", [])
        if not choices:
            await self.on_chunk_complete(chunk, state, context)
            return

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            await self.on_chunk_complete(chunk, state, context)
            return

        # Extract delta
        delta = first_choice.get("delta", {})
        if not isinstance(delta, dict):
            await self.on_chunk_complete(chunk, state, context)
            return

        # Hook: role delta
        role = delta.get("role")
        if role and isinstance(role, str):
            await self.on_role_delta(role, chunk, state, context)
            if context._terminate_flag:
                return

        # Hook: content chunk
        content = delta.get("content")
        if content and isinstance(content, str):
            await self.on_content_chunk(content, chunk, state, context)
            if context._terminate_flag:
                return

        # Hook: tool call deltas
        tool_calls = delta.get("tool_calls")
        if tool_calls and isinstance(tool_calls, list):
            for tc_delta in tool_calls:
                if isinstance(tc_delta, dict):
                    await self.on_tool_call_delta(tc_delta, chunk, state, context)
                    if context._terminate_flag:
                        return

        # Hook: usage delta
        usage = chunk_dict.get("usage")
        if usage and isinstance(usage, dict):
            await self.on_usage_delta(usage, chunk, state, context)
            if context._terminate_flag:
                return

        # Hook: finish reason
        finish_reason = first_choice.get("finish_reason")
        if finish_reason:
            await self.on_finish_reason(str(finish_reason), chunk, state, context)
            if context._terminate_flag:
                return

        # Hook: chunk complete
        await self.on_chunk_complete(chunk, state, context)


__all__ = [
    "EventDrivenPolicy",
    "StreamingContext",
    "TerminateStream",
]
