# ABOUTME: High-level helper for tool call streaming policies
# ABOUTME: Owns outgoing queue writes and provides callbacks for business logic

"""Tool call streaming gate for policy authors.

This module provides ToolCallStreamGate, a high-level helper that:
1. Manages the event iterator and chunk aggregation
2. Owns all outgoing queue writes (callbacks return decisions, never write directly)
3. Exposes simple callbacks for content, tool calls, errors, and stream end
4. Handles buffer forwarding and queue shutdown automatically

This is the "easy mode" for writing tool call judging policies without
manually managing streaming state.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

from litellm.types.utils import ModelResponse

from luthien_proxy.utils.streaming_aggregation import StreamChunkAggregator
from luthien_proxy.v2.streaming.events import (
    ContentChunk,
    OtherChunk,
    StreamClosed,
    StreamError,
    ToolCallDelta,
    iter_events,
)


@dataclass(frozen=True)
class ToolCall:
    """Normalized tool call representation.

    Attributes:
        index: Tool call index (for parallel tool calls)
        tool_id: Tool call identifier
        call_type: Type of call (typically "function")
        name: Complete tool name
        arguments: Complete arguments (JSON string)
        is_incomplete: Whether this tool call was incomplete when stream ended
    """

    index: int
    tool_id: str
    call_type: str
    name: str
    arguments: str
    is_incomplete: bool = False


@dataclass(frozen=True)
class GateDecision:
    """Decision returned by gate callbacks.

    Attributes:
        allow: Whether to allow the event through
        replacement: If blocked, optional replacement chunk to send instead
        terminate: If True, terminate stream after sending replacement
    """

    allow: bool = True
    replacement: ModelResponse | None = None
    terminate: bool = False


class ToolCallStreamGate:
    """High-level helper for streaming tool call policies.

    This gate manages the streaming loop, aggregates tool calls, and
    provides callbacks for policy authors to implement business logic.

    The gate owns all writes to the outgoing queue - callbacks return
    decisions but never write directly. This prevents double-writes and
    missed shutdowns.

    Example:
        gate = ToolCallStreamGate(
            on_tool_complete=lambda tc: evaluate_and_decide(tc),
        )
        await gate.process(incoming, outgoing, keepalive)
    """

    def __init__(
        self,
        on_content: Callable[[str, ModelResponse], GateDecision | asyncio.Future[GateDecision]] | None = None,
        on_tool_complete: Callable[[ToolCall], GateDecision | asyncio.Future[GateDecision]] | None = None,
        on_error: Callable[[Exception], GateDecision | asyncio.Future[GateDecision]] | None = None,
        on_closed: Callable[[], None] | None = None,
    ):
        """Initialize gate with callbacks.

        Callbacks can be sync or async. Async callbacks should be coroutine functions.

        Args:
            on_content: Called for each content chunk. Receives (text, raw_chunk).
                Default: allow through. Can be sync or async.
            on_tool_complete: Called when a complete tool call is ready. Receives ToolCall.
                Default: allow through. Can be sync or async.
            on_error: Called when stream error occurs. Receives exception.
                Default: allow through (no error sent). Can be sync or async.
            on_closed: Called when stream ends normally (no decision returned).
                Can be sync or async.
        """
        self._on_content = on_content or (lambda text, chunk: GateDecision(allow=True))
        self._on_tool_complete = on_tool_complete or (lambda tc: GateDecision(allow=True))
        self._on_error = on_error or (lambda exc: GateDecision(allow=True))
        self._on_closed = on_closed or (lambda: None)

        # Internal state - aggregators per tool call index
        self._aggregators: dict[int, StreamChunkAggregator] = {}
        self._tool_call_buffers: dict[int, list[ModelResponse]] = {}
        self._completed_tool_calls: set[int] = set()

    async def _invoke_callback(self, callback: Callable, *args) -> Any:
        """Invoke a callback that may be sync or async.

        Args:
            callback: Callback function (sync or async)
            *args: Arguments to pass to callback

        Returns:
            Result from callback
        """
        result = callback(*args)
        if asyncio.iscoroutine(result):
            return await result
        return result

    async def process(
        self,
        incoming: asyncio.Queue[ModelResponse],
        outgoing: asyncio.Queue[ModelResponse],
        keepalive: Callable[[], None] | None = None,
    ) -> None:
        """Process streaming chunks through the gate.

        This method runs the streaming loop, invoking callbacks and managing
        outgoing writes. It shuts down the outgoing queue when done.

        Args:
            incoming: Queue of chunks from LLM (shut down when stream ends)
            outgoing: Queue of chunks to send to client (gate owns writes)
            keepalive: Optional callback to prevent timeout during long operations
        """
        try:
            async for event in iter_events(incoming):
                match event:
                    case ContentChunk(content=text, raw_chunk=chunk):
                        decision = await self._invoke_callback(self._on_content, text, chunk)
                        if not await self._handle_decision(decision, chunk, outgoing):
                            return  # Terminate stream

                    case OtherChunk(raw_chunk=chunk):
                        # Check if this is a tool_calls finish chunk - if so, trigger completion check
                        chunk_dict = chunk.model_dump() if hasattr(chunk, "model_dump") else dict(chunk)  # type: ignore
                        choices = chunk_dict.get("choices", [])
                        if choices and isinstance(choices[0], dict):
                            finish_reason = choices[0].get("finish_reason")
                            if finish_reason == "tool_calls" and self._tool_call_buffers:
                                # Finish chunk for tool calls - buffer it and check for completion
                                for idx in list(self._tool_call_buffers.keys()):
                                    if idx not in self._completed_tool_calls:
                                        self._tool_call_buffers[idx].append(chunk)
                                        # Update aggregator to capture finish_reason
                                        self._aggregators[idx].capture_chunk(chunk_dict)

                                        # Tool call is now complete - evaluate it
                                        agg = self._aggregators[idx]
                                        self._completed_tool_calls.add(idx)

                                        tool_call_states = list(agg.tool_calls.values())
                                        if tool_call_states:
                                            state = tool_call_states[0]
                                            tool_call = ToolCall(
                                                index=idx,
                                                tool_id=state.identifier,
                                                call_type=state.call_type,
                                                name=state.name,
                                                arguments=state.arguments,
                                            )

                                            if keepalive:
                                                keepalive()

                                            decision = await self._invoke_callback(self._on_tool_complete, tool_call)

                                            if decision.allow:
                                                for buffered_chunk in self._tool_call_buffers[idx]:
                                                    await outgoing.put(buffered_chunk)
                                                self._tool_call_buffers[idx].clear()
                                            else:
                                                self._tool_call_buffers[idx].clear()
                                                if decision.replacement:
                                                    await outgoing.put(decision.replacement)
                                                if decision.terminate:
                                                    return
                                # Don't forward the finish chunk separately - it's in the buffer
                                continue

                        # Forward other chunks (role, non-tool-call finish, metadata) directly
                        await outgoing.put(chunk)

                    case ToolCallDelta(index=idx, raw_chunk=chunk):
                        # Buffer chunk and aggregate
                        if idx not in self._aggregators:
                            self._aggregators[idx] = StreamChunkAggregator()
                            self._tool_call_buffers[idx] = []

                        chunk_dict = chunk.model_dump() if hasattr(chunk, "model_dump") else dict(chunk)  # type: ignore
                        self._aggregators[idx].capture_chunk(chunk_dict)
                        self._tool_call_buffers[idx].append(chunk)

                        # Check if tool call is complete (only when finish_reason arrives)
                        if idx not in self._completed_tool_calls:
                            agg = self._aggregators[idx]
                            if agg.finish_reason == "tool_calls":
                                self._completed_tool_calls.add(idx)

                                # Extract tool call data
                                tool_call_states = list(agg.tool_calls.values())
                                if tool_call_states:
                                    state = tool_call_states[0]  # Single tool call per index
                                    tool_call = ToolCall(
                                        index=idx,
                                        tool_id=state.identifier,
                                        call_type=state.call_type,
                                        name=state.name,
                                        arguments=state.arguments,
                                    )

                                    if keepalive:
                                        keepalive()

                                    # Invoke callback
                                    decision = await self._invoke_callback(self._on_tool_complete, tool_call)

                                    # If allowed, forward buffered chunks
                                    if decision.allow:
                                        for buffered_chunk in self._tool_call_buffers[idx]:
                                            await outgoing.put(buffered_chunk)
                                        self._tool_call_buffers[idx].clear()
                                    else:
                                        # Blocked - clear buffer and handle replacement
                                        self._tool_call_buffers[idx].clear()
                                        if decision.replacement:
                                            await outgoing.put(decision.replacement)
                                        if decision.terminate:
                                            return

                    case StreamError(error=exc):
                        decision = await self._invoke_callback(self._on_error, exc)
                        if not await self._handle_decision(decision, None, outgoing):
                            return

                    case StreamClosed():
                        # Flush any remaining buffered chunks (incomplete tool calls)
                        for idx, buffer in self._tool_call_buffers.items():
                            if idx not in self._completed_tool_calls and buffer:
                                # Incomplete tool call - evaluate it
                                agg = self._aggregators[idx]
                                tool_call_states = list(agg.tool_calls.values())
                                if tool_call_states:
                                    state = tool_call_states[0]
                                    tool_call = ToolCall(
                                        index=idx,
                                        tool_id=state.identifier,
                                        call_type=state.call_type,
                                        name=state.name,
                                        arguments=state.arguments,
                                        is_incomplete=True,  # Mark as incomplete (stream ended early)
                                    )

                                    if keepalive:
                                        keepalive()

                                    decision = await self._invoke_callback(self._on_tool_complete, tool_call)

                                    if decision.allow:
                                        for buffered_chunk in buffer:
                                            await outgoing.put(buffered_chunk)
                                    else:
                                        if decision.replacement:
                                            await outgoing.put(decision.replacement)
                                        if decision.terminate:
                                            return

                        await self._invoke_callback(self._on_closed)
                        break

        finally:
            # Gate owns outgoing shutdown
            outgoing.shutdown()

    async def _handle_decision(
        self, decision: GateDecision, original_chunk: ModelResponse | None, outgoing: asyncio.Queue[ModelResponse]
    ) -> bool:
        """Handle a gate decision.

        Args:
            decision: Decision from callback
            original_chunk: Original chunk to forward if allowed
            outgoing: Outgoing queue

        Returns:
            True to continue stream, False to terminate
        """
        if decision.allow and original_chunk:
            await outgoing.put(original_chunk)
        elif not decision.allow and decision.replacement:
            await outgoing.put(decision.replacement)

        return not decision.terminate

    def _is_tool_call_complete(self, agg: StreamChunkAggregator) -> bool:
        """Check if aggregator has a complete tool call.

        Args:
            agg: Aggregator to check

        Returns:
            True if tool call has name (minimum for evaluation)
        """
        if not agg.tool_calls:
            return False

        # Tool call is complete enough if it has a name
        for state in agg.tool_calls.values():
            if state.name:
                return True

        return False


__all__ = ["ToolCall", "GateDecision", "ToolCallStreamGate"]
