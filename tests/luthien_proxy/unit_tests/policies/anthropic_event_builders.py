"""Shared Anthropic streaming event builders for policy unit tests.

These construct the protocol-level primitives (content_block_start, delta, stop,
message_delta) that any Anthropic streaming policy test needs.
"""

from __future__ import annotations

from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    TextBlock,
    TextDelta,
    ToolUseBlock,
    Usage,
)


def text_start(index: int = 0) -> RawContentBlockStartEvent:
    return RawContentBlockStartEvent(
        type="content_block_start",
        index=index,
        content_block=TextBlock(type="text", text=""),
    )


def text_delta(text: str, index: int = 0) -> RawContentBlockDeltaEvent:
    return RawContentBlockDeltaEvent(
        type="content_block_delta",
        index=index,
        delta=TextDelta(type="text_delta", text=text),
    )


def tool_start(index: int = 0, tool_id: str = "toolu_abc", name: str = "Bash") -> RawContentBlockStartEvent:
    return RawContentBlockStartEvent(
        type="content_block_start",
        index=index,
        content_block=ToolUseBlock(type="tool_use", id=tool_id, name=name, input={}),
    )


def tool_delta(partial_json: str, index: int = 0) -> RawContentBlockDeltaEvent:
    return RawContentBlockDeltaEvent(
        type="content_block_delta",
        index=index,
        delta=InputJSONDelta(type="input_json_delta", partial_json=partial_json),
    )


def block_stop(index: int = 0) -> RawContentBlockStopEvent:
    return RawContentBlockStopEvent(type="content_block_stop", index=index)


def message_delta(stop_reason: str = "end_turn") -> RawMessageDeltaEvent:
    from anthropic.types.raw_message_delta_event import Delta

    return RawMessageDeltaEvent.model_construct(
        type="message_delta",
        delta=Delta.model_construct(stop_reason=stop_reason, stop_sequence=None),
        usage=Usage(input_tokens=0, output_tokens=10),
    )


def event_types(events: list[MessageStreamEvent]) -> list[str]:
    """Extract event type strings for easy assertion."""
    return [getattr(e, "type", None) for e in events]
