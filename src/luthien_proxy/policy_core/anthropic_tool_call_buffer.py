"""Policy-agnostic Anthropic tool_use buffer with caller-supplied transform.

A per-request streaming filter that lets callers transform a model's tool calls
without writing event-state-machine code. Text content streams through in real
time; tool_use blocks are buffered until the upstream response is complete; the
caller's transform closure receives the full list of buffered tool calls and
returns the content blocks to emit in their place.

Cross-event invariants (output indices, `stop_reason` consistency) live inside
the buffer so individual policies cannot drop them.

Ordering caveat: text passes through immediately to preserve streaming UX. Tool
replacements are emitted after all upstream events have been seen (at the
`message_delta`). Upstream order `text, tool_use, text` becomes output order
`text, text, <transform output>`. This matches typical Claude responses
(text-then-tools); a future `buffer_all` mode could preserve exact ordering at
the cost of streaming latency.

Error-handling caveat: exceptions raised by the transform (or by `_emit_block`
when the transform returns an unsupported block type) propagate out of
`process()`. By that point any earlier text content has already been streamed
to the client, so the client sees a partial response with no terminating
`message_delta` / `message_stop`. Transforms should be written to not raise on
normal data; the pipeline above is responsible for surfacing the error to the
client connection.

Usage:
    buf = ctx.get_request_state(
        self, ToolCallStreamBuffer, lambda: ToolCallStreamBuffer(self._decide)
    )
    return await buf.process(event)

    async def _decide(self, tool_calls: list[BufferedToolCall]) -> list[AnthropicContentBlock]:
        ...
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

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
)

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicContentBlock,
        AnthropicResponse,
        JSONObject,
    )

logger = logging.getLogger(__name__)


@dataclass
class BufferedToolCall:
    """A tool_use block being (or done being) buffered from the upstream stream."""

    id: str
    name: str
    input_json: str = ""

    @property
    def input(self) -> "JSONObject":
        """Parsed input dict, or `{"_raw": input_json}` if the JSON is malformed."""
        if not self.input_json:
            return {}
        try:
            parsed = json.loads(self.input_json)
        except json.JSONDecodeError:
            return cast("JSONObject", {"_raw": self.input_json})
        if not isinstance(parsed, dict):
            return cast("JSONObject", {"_raw": self.input_json})
        return cast("JSONObject", parsed)

    def as_content_block(self) -> "AnthropicContentBlock":
        """Return the dict-shaped content block for passthrough in a transform."""
        return cast(
            "AnthropicContentBlock",
            {"type": "tool_use", "id": self.id, "name": self.name, "input": self.input},
        )


ToolCallTransform = Callable[[list[BufferedToolCall]], Awaitable[list["AnthropicContentBlock"]]]
"""Caller-supplied async function: receives buffered tool calls, returns replacement content blocks.

Supported output block types: text and tool_use. Other block types raise ValueError
when emitted (the model never emits image/thinking via tool-call-shaped responses,
and the buffer's job is specifically to rewrite tool calls).
"""


@dataclass
class _BufferState:
    """Internal mutable state for one streaming response."""

    buffered: list[BufferedToolCall] = field(default_factory=list)
    upstream_to_buf_pos: dict[int, int] = field(default_factory=dict)
    passthrough_index_map: dict[int, int] = field(default_factory=dict)
    next_output_index: int = 0
    transform_invoked: bool = False


class ToolCallStreamBuffer:
    """Per-request streaming filter over Anthropic message events.

    Construct one per streaming response (typically via
    `PolicyContext.get_request_state`). Call `process(event)` for every upstream
    event in order; emit the returned events downstream.
    """

    def __init__(self, transform: ToolCallTransform) -> None:
        """Bind the transform closure that will be invoked at message_delta."""
        self._transform = transform
        self._state = _BufferState()

    async def process(self, event: MessageStreamEvent) -> list[MessageStreamEvent]:
        """Filter one upstream event; return zero or more downstream events."""
        if isinstance(event, RawContentBlockStartEvent):
            return self._on_block_start(event)
        if isinstance(event, RawContentBlockDeltaEvent):
            return self._on_block_delta(event)
        if isinstance(event, RawContentBlockStopEvent):
            return self._on_block_stop(event)
        if isinstance(event, RawMessageDeltaEvent):
            return await self._on_message_delta(event)
        return [event]

    def _on_block_start(self, event: RawContentBlockStartEvent) -> list[MessageStreamEvent]:
        if isinstance(event.content_block, ToolUseBlock):
            self._state.upstream_to_buf_pos[event.index] = len(self._state.buffered)
            self._state.buffered.append(BufferedToolCall(id=event.content_block.id, name=event.content_block.name))
            return []
        output_index = self._allocate_output_index()
        self._state.passthrough_index_map[event.index] = output_index
        rewritten = event.model_copy(update={"index": output_index})
        return [cast(MessageStreamEvent, rewritten)]

    def _on_block_delta(self, event: RawContentBlockDeltaEvent) -> list[MessageStreamEvent]:
        pos = self._state.upstream_to_buf_pos.get(event.index)
        if pos is not None:
            if isinstance(event.delta, InputJSONDelta):
                self._state.buffered[pos].input_json += event.delta.partial_json
            else:
                logger.warning(
                    "Non-InputJSONDelta arrived on buffered tool_use index %d; dropping",
                    event.index,
                )
            return []
        output_index = self._state.passthrough_index_map.get(event.index)
        if output_index is None:
            logger.warning("Delta for unknown upstream index %d; passing through", event.index)
            return [cast(MessageStreamEvent, event)]
        rewritten = event.model_copy(update={"index": output_index})
        return [cast(MessageStreamEvent, rewritten)]

    def _on_block_stop(self, event: RawContentBlockStopEvent) -> list[MessageStreamEvent]:
        if event.index in self._state.upstream_to_buf_pos:
            return []
        output_index = self._state.passthrough_index_map.get(event.index)
        if output_index is None:
            logger.warning("Stop for unknown upstream index %d; passing through", event.index)
            return [cast(MessageStreamEvent, event)]
        rewritten = event.model_copy(update={"index": output_index})
        return [cast(MessageStreamEvent, rewritten)]

    async def _on_message_delta(self, event: RawMessageDeltaEvent) -> list[MessageStreamEvent]:
        if self._state.transform_invoked:
            logger.warning("message_delta arrived twice; passing through second occurrence")
            return [cast(MessageStreamEvent, event)]
        self._state.transform_invoked = True

        if not self._state.buffered:
            return [cast(MessageStreamEvent, event)]

        output_blocks = await self._transform(self._state.buffered)
        emitted_events: list[MessageStreamEvent] = []
        for block in output_blocks:
            emitted_events.extend(self._emit_block(block))

        any_tool_use = any(_is_tool_use_block(b) for b in output_blocks)
        upstream_stop = event.delta.stop_reason
        new_stop = _adjust_stop_reason(upstream_stop, any_tool_use)
        if new_stop != upstream_stop:
            event = RawMessageDeltaEvent.model_construct(
                type="message_delta",
                delta=event.delta.model_copy(update={"stop_reason": new_stop}),
                usage=event.usage,
            )
        emitted_events.append(cast(MessageStreamEvent, event))
        return emitted_events

    def _allocate_output_index(self) -> int:
        idx = self._state.next_output_index
        self._state.next_output_index += 1
        return idx

    def _emit_block(self, block: "AnthropicContentBlock") -> list[MessageStreamEvent]:
        block_dict = cast(dict, block)
        block_type = block_dict.get("type")
        index = self._allocate_output_index()
        if block_type == "text":
            return _events_for_text(index, str(block_dict.get("text", "")))
        if block_type == "tool_use":
            return _events_for_tool_use(
                index,
                tool_id=str(block_dict["id"]),
                name=str(block_dict["name"]),
                tool_input=block_dict.get("input", {}),
            )
        raise ValueError(
            f"ToolCallStreamBuffer transform returned unsupported block type {block_type!r}; "
            "only 'text' and 'tool_use' are supported."
        )


async def transform_anthropic_response(
    response: "AnthropicResponse",
    transform: ToolCallTransform,
) -> "AnthropicResponse":
    """Non-streaming sibling of `ToolCallStreamBuffer`.

    Walks `response["content"]`, extracts tool_use blocks as `BufferedToolCall`s,
    invokes `transform` with them, and stitches the result back into the content
    list in place of the original tool_uses. Non-tool-use blocks (text, thinking,
    etc.) are preserved at their original positions; the transform output is
    inserted at the first tool_use position and any subsequent tool_uses are
    dropped (matching the streaming-side "tool replacements come together"
    semantics). `stop_reason` is rewritten to match the final content shape.
    """
    content = response.get("content") or []
    tool_calls: list[BufferedToolCall] = []
    first_tool_idx: int | None = None
    rebuilt: list["AnthropicContentBlock"] = []

    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            tool_calls.append(
                BufferedToolCall(
                    id=str(block.get("id", "")),
                    name=str(block.get("name", "")),
                    input_json=json.dumps(block.get("input", {})),
                )
            )
            if first_tool_idx is None:
                first_tool_idx = len(rebuilt)
                rebuilt.append(cast("AnthropicContentBlock", {"__TOOL_PLACEHOLDER__": True}))
        else:
            rebuilt.append(block)

    if not tool_calls:
        return response

    new_blocks = await transform(tool_calls)
    assert first_tool_idx is not None  # tool_calls non-empty implies a placeholder exists
    rebuilt = rebuilt[:first_tool_idx] + list(new_blocks) + rebuilt[first_tool_idx + 1 :]

    any_tool_use = any(_is_tool_use_block(b) for b in rebuilt)
    upstream_stop = response.get("stop_reason")
    new_stop = _adjust_stop_reason(upstream_stop, any_tool_use)

    # Identity-preserving fast path: if the transform was a pure passthrough and
    # the stop_reason didn't need adjusting, return the original response object.
    if rebuilt == list(content) and new_stop == upstream_stop:
        return response

    modified = dict(response)
    modified["content"] = rebuilt
    if new_stop != upstream_stop:
        modified["stop_reason"] = new_stop
    return cast("AnthropicResponse", modified)


def _is_tool_use_block(block: "AnthropicContentBlock") -> bool:
    return isinstance(block, dict) and block.get("type") == "tool_use"


def _adjust_stop_reason(upstream: str | None, any_tool_use: bool) -> str | None:
    """If the post-transform content has no tool_use, rewrite `tool_use` to `end_turn`.

    The reverse direction (adding tool_use to a non-tool_use response) is not
    auto-rewritten because the upstream knew its own stop_reason for whatever
    real reason; promoting `end_turn` to `tool_use` could mask a legitimate
    `max_tokens` truncation. Callers that synthesize tool calls must set
    stop_reason themselves at a higher layer.
    """
    if upstream == "tool_use" and not any_tool_use:
        return "end_turn"
    return upstream


def _events_for_text(index: int, text: str) -> list[MessageStreamEvent]:
    start = RawContentBlockStartEvent(
        type="content_block_start", index=index, content_block=TextBlock(type="text", text="")
    )
    delta = RawContentBlockDeltaEvent.model_construct(
        type="content_block_delta",
        index=index,
        delta=TextDelta.model_construct(type="text_delta", text=text),
    )
    stop = RawContentBlockStopEvent(type="content_block_stop", index=index)
    return [cast(MessageStreamEvent, start), cast(MessageStreamEvent, delta), cast(MessageStreamEvent, stop)]


def _events_for_tool_use(index: int, *, tool_id: str, name: str, tool_input: object) -> list[MessageStreamEvent]:
    start = RawContentBlockStartEvent(
        type="content_block_start",
        index=index,
        content_block=ToolUseBlock(type="tool_use", id=tool_id, name=name, input={}),
    )
    json_payload = json.dumps(tool_input) if tool_input else "{}"
    delta = RawContentBlockDeltaEvent(
        type="content_block_delta",
        index=index,
        delta=InputJSONDelta(type="input_json_delta", partial_json=json_payload),
    )
    stop = RawContentBlockStopEvent(type="content_block_stop", index=index)
    return [cast(MessageStreamEvent, start), cast(MessageStreamEvent, delta), cast(MessageStreamEvent, stop)]


__all__ = [
    "BufferedToolCall",
    "ToolCallStreamBuffer",
    "ToolCallTransform",
    "transform_anthropic_response",
]
