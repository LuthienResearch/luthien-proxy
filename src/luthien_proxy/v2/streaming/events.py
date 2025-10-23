# ABOUTME: Streaming event schema for policy authors
# ABOUTME: Provides typed events from LiteLLM chunks with raw chunk access

"""Streaming event schema for policy authors.

This module defines a high-level event schema over raw LiteLLM chunks,
making it easier to write streaming policies without dealing with chunk
parsing details. All events include the raw chunk for advanced use cases.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator

from litellm.types.utils import ModelResponse


@dataclass(frozen=True)
class StreamStarted:
    """First event when stream begins.

    Attributes:
        raw_chunk: The raw ModelResponse chunk
    """

    raw_chunk: ModelResponse


@dataclass(frozen=True)
class ContentChunk:
    """Chunk containing text content delta.

    Attributes:
        content: Text content from this chunk
        raw_chunk: The raw ModelResponse chunk
    """

    content: str
    raw_chunk: ModelResponse


@dataclass(frozen=True)
class ToolCallDelta:
    """Incremental update to a tool call.

    Attributes:
        index: Tool call index (for parallel tool calls)
        tool_id: Tool call identifier (may be empty in early deltas)
        call_type: Type of call (typically "function")
        name_delta: Incremental name text (if any)
        arguments_delta: Incremental arguments text (if any)
        raw_chunk: The raw ModelResponse chunk
    """

    index: int
    tool_id: str
    call_type: str
    name_delta: str
    arguments_delta: str
    raw_chunk: ModelResponse


@dataclass(frozen=True)
class ToolCallComplete:
    """Signals a tool call has finished streaming.

    Attributes:
        index: Tool call index
        tool_id: Tool call identifier
        call_type: Type of call (typically "function")
        name: Complete tool name
        arguments: Complete arguments (JSON string)
        raw_chunk: The raw ModelResponse chunk with finish_reason
    """

    index: int
    tool_id: str
    call_type: str
    name: str
    arguments: str
    raw_chunk: ModelResponse


@dataclass(frozen=True)
class StreamError:
    """Error occurred during streaming.

    Attributes:
        error: Exception that occurred
        raw_chunk: The raw ModelResponse chunk (if available)
    """

    error: Exception
    raw_chunk: ModelResponse | None


@dataclass(frozen=True)
class OtherChunk:
    """Chunk that doesn't contain content or tool call deltas.

    These are typically role-only chunks, finish chunks, or metadata chunks.

    Attributes:
        raw_chunk: The raw ModelResponse chunk
    """

    raw_chunk: ModelResponse


@dataclass(frozen=True)
class StreamClosed:
    """Stream has ended normally.

    Attributes:
        finish_reason: Reason stream ended (e.g., "stop", "tool_calls")
        raw_chunk: The final ModelResponse chunk (if available)
    """

    finish_reason: str | None
    raw_chunk: ModelResponse | None


StreamEvent = StreamStarted | ContentChunk | ToolCallDelta | ToolCallComplete | OtherChunk | StreamError | StreamClosed


async def iter_events(incoming: asyncio.Queue[ModelResponse]) -> AsyncIterator[StreamEvent]:
    """Convert raw LiteLLM chunks into typed events.

    This helper consumes chunks from a queue and yields high-level events.
    Policies can use these events for readable branching logic while retaining
    access to raw chunks when needed.

    Args:
        incoming: Queue of ModelResponse chunks (shut down when stream ends)

    Yields:
        StreamEvent instances representing stream lifecycle

    Example:
        async for event in iter_events(incoming_queue):
            match event:
                case ContentChunk(content=text):
                    # Process text content
                    await outgoing.put(event.raw_chunk)
                case ToolCallComplete(name=tool_name, arguments=args):
                    # Evaluate complete tool call
                    if should_block(tool_name, args):
                        await outgoing.put(create_blocked_response())
                        return
                case StreamClosed():
                    break
    """
    first_chunk = True
    last_finish_reason: str | None = None

    try:
        while True:
            try:
                chunk = await incoming.get()
            except asyncio.QueueShutDown:
                break

            # Emit StreamStarted for first chunk
            if first_chunk:
                first_chunk = False
                yield StreamStarted(raw_chunk=chunk)

            # Convert chunk to dict for parsing
            chunk_dict = chunk.model_dump() if hasattr(chunk, "model_dump") else dict(chunk)  # type: ignore

            # Extract choices
            choices = chunk_dict.get("choices", [])
            if not choices:
                continue

            first_choice = choices[0]
            if not isinstance(first_choice, dict):
                continue

            # Check for finish_reason
            finish_reason = first_choice.get("finish_reason")
            if finish_reason:
                last_finish_reason = finish_reason

            # Parse delta
            delta = first_choice.get("delta", {})
            if not isinstance(delta, dict):
                continue

            # Track if we emitted any specific event for this chunk
            emitted_event = False

            # Emit ContentChunk if content present
            content = delta.get("content")
            if content and isinstance(content, str):
                yield ContentChunk(content=content, raw_chunk=chunk)
                emitted_event = True

            # Emit ToolCallDelta events
            tool_calls = delta.get("tool_calls")
            if tool_calls and isinstance(tool_calls, list):
                for tc_delta in tool_calls:
                    if not isinstance(tc_delta, dict):
                        continue

                    index = tc_delta.get("index", 0)
                    tool_id = tc_delta.get("id", "")
                    call_type = tc_delta.get("type", "function")

                    # Extract function delta
                    function_delta = tc_delta.get("function", {})
                    if not isinstance(function_delta, dict):
                        function_delta = {}

                    name_delta = function_delta.get("name", "")
                    arguments_delta = function_delta.get("arguments", "")

                    yield ToolCallDelta(
                        index=index,
                        tool_id=tool_id,
                        call_type=call_type,
                        name_delta=name_delta,
                        arguments_delta=arguments_delta,
                        raw_chunk=chunk,
                    )
                    emitted_event = True

            # If no specific event was emitted, yield OtherChunk
            # This handles role-only chunks, finish chunks, etc.
            if not emitted_event:
                yield OtherChunk(raw_chunk=chunk)

    except Exception as error:
        yield StreamError(error=error, raw_chunk=None)
    finally:
        yield StreamClosed(finish_reason=last_finish_reason, raw_chunk=None)


__all__ = [
    "StreamEvent",
    "StreamStarted",
    "ContentChunk",
    "ToolCallDelta",
    "ToolCallComplete",
    "OtherChunk",
    "StreamError",
    "StreamClosed",
    "iter_events",
]
