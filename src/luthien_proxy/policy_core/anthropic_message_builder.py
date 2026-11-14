"""Anthropic assistant-message builder that enforces wire invariants.

Builds an Anthropic-compliant assistant message from a stream of policy
decisions. The builder owns the wire-protocol invariants — policies feed it
abstract decisions (commit text, buffer tool, block tool, note judge
unavailable) and the builder produces SSE events in a legal order. The
same primitives also drive the non-streaming path via
`to_anthropic_response()`.

The key invariant: once any `tool_use` block reaches the wire, no non-tool
content may follow (Anthropic 400s on the next turn — see issue #708). The
builder enforces this by *buffering* `tool_use` decisions until `finalize()`
is called. Text and passthrough blocks (thinking, redacted_thinking) commit
to the wire immediately *if no tool has been buffered yet*; once any tool
is buffered, subsequent text decisions are also queued for emission before
the tool flush. Either way, the wire ends with `tool_use` blocks.

Reasoning blocks are the exception. A `thinking` or `redacted_thinking`
block that upstream sent after a `tool_use` is queued with the tools and
keeps its upstream position (interleaved thinking), because the next turn
replays the blocks as sent. When thinking is enabled, a `tool_use` must
follow the thinking that led to it, so if any thinking block was dropped the
builder blocks the tool calls of that turn as well.

Other invariants the builder owns:

- Indices are assigned monotonically by the builder; callers never compute
  them. The builder maintains an upstream→downstream map for passthrough
  blocks so deltas/stops with the same upstream index land on the right
  downstream slot.
- The judge-unavailable warning and the consolidated blocked-tool marker
  emit at `finalize()` in the pre-tool slot, so they can never violate the
  trailing-tool_use invariant regardless of when they were noted.
- `stop_reason` is corrected at `finalize()` / `to_anthropic_response()`
  *only* when upstream is known wrong given what we emitted: a
  `tool_use` reason with no buffered tool becomes `end_turn`. Every
  other reason (`max_tokens`, `stop_sequence`, `refusal`, etc.) is
  preserved — clients depend on those signals.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypeAlias, cast

from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    TextBlock,
    TextDelta,
    ThinkingDelta,
    ToolUseBlock,
)

from luthien_proxy.policy_core.block_descriptor import BlockDescriptor

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicContentBlock,
        AnthropicResponse,
        JSONObject,
    )

logger = logging.getLogger(__name__)

ToolStopHandler = Callable[["AnthropicMessageBuilder", "BufferedTool"], Awaitable[list[MessageStreamEvent]]]
"""Caller-supplied async hook invoked at each tool_use block_stop.

Receives the builder and the assembled `BufferedTool`. Should call the
builder primitives (`commit_text`, `buffer_tool`, `record_blocked_tool`)
to record its decision and return any events emitted by those calls.
"""


ToolBlockHandler = Callable[["AnthropicMessageBuilder", "BufferedTool"], Awaitable[None]]
"""Async hook for `compose_tool_only_response`: receives the builder and a
`BufferedTool` per upstream tool_use block; should record a decision on
the builder. Returns None — the non-streaming caller doesn't need events."""


async def compose_tool_only_response(
    response: "AnthropicResponse",
    on_tool: ToolBlockHandler,
) -> "AnthropicResponse":
    """Non-streaming sibling of `AnthropicMessageBuilder.dispatch_tool_only`.

    Walks `response["content"]`, hands each tool_use to `on_tool` (which
    drives a fresh builder), and asks the builder for the finalized
    response. Non-tool blocks (text, thinking, etc.) are committed
    verbatim into the pre-tool slot. Returns the original response
    unchanged when content has no tool_use blocks.
    """
    content = response.get("content") or []
    if not content or not any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content):
        return response

    builder = AnthropicMessageBuilder()
    for block in content:
        if not isinstance(block, dict):
            builder.commit_raw_block(block)
            continue
        if block.get("type") == "tool_use":
            tool_input = block.get("input", {})
            input_json = tool_input if isinstance(tool_input, str) else json.dumps(tool_input) if tool_input else "{}"
            tool = BufferedTool(
                id=str(block.get("id", "")),
                name=str(block.get("name", "")),
                input_json=input_json,
            )
            await on_tool(builder, tool)
        elif block.get("type") == "text":
            builder.commit_text(block.get("text", ""))
        else:
            builder.commit_raw_block(block)
    return builder.to_anthropic_response(response)


@dataclass(frozen=True)
class _QueuedTool:
    id: str
    name: str
    input_json: str


@dataclass(frozen=True)
class _BlockedToolRecord:
    name: str
    judge_failed: bool


@dataclass
class _BufferedTextBlock:
    """Upstream text block being accumulated from streaming deltas."""

    text: str = ""


@dataclass
class BufferedThinking:
    """Upstream thinking block held back from the wire until it is judged.

    The original start and delta events are kept verbatim so a judged-pass
    block is re-emitted with its ``signature`` intact; ``text`` is the
    accumulated reasoning the judge sees.
    """

    start_event: RawContentBlockStartEvent
    deltas: list[RawContentBlockDeltaEvent] = field(default_factory=list)
    text: str = ""


@dataclass
class BufferedTool:
    """Upstream tool_use block accumulated from streaming deltas.

    Distinct from `_QueuedTool` (the post-judge tool waiting for wire flush).
    Returned by `AnthropicMessageBuilder.take_tool()`.
    """

    id: str
    name: str
    input_json: str = ""

    @property
    def parsed_input(self) -> "JSONObject":
        """Parsed input dict, or `{"_raw": input_json}` for malformed JSON."""
        return parse_tool_input(self.input_json)


_TailItem: TypeAlias = "_QueuedTool | BufferedThinking | AnthropicContentBlock"


@dataclass
class _BuilderState:
    # Upstream buffering: deltas accumulate here before being judged.
    text_buffer: dict[int, _BufferedTextBlock] = field(default_factory=dict)
    thinking_buffer: dict[int, BufferedThinking] = field(default_factory=dict)
    tool_buffer: dict[int, BufferedTool] = field(default_factory=dict)
    # Downstream composition: where committed/queued content lives until flush.
    committed_descriptors: list[BlockDescriptor] = field(default_factory=list)
    pre_tool_blocks: list["AnthropicContentBlock"] = field(default_factory=list)
    tail: list[_TailItem] = field(default_factory=list)
    blocked_tools: list[_BlockedToolRecord] = field(default_factory=list)
    dropped_thinking: int = 0
    dropped_thinking_judge_failed: bool = False
    pending_warning_text: str | None = None
    pending_fallback_text: str | None = None
    next_output_index: int = 0
    passthrough_index_map: dict[int, int] = field(default_factory=dict)
    finalized: bool = False


class AnthropicMessageBuilder:
    """Streaming builder that produces a wire-correct Anthropic assistant message.

    Lifecycle:

    1. For each upstream/policy decision, call one of:
        - `commit_text(text)` — record a text block (emits now or buffers)
        - `buffer_tool(id, name, input_json)` — record a tool_use (always buffers)
        - `record_blocked_tool(name, judge_failed)` — record that a tool was blocked
        - `commit_thinking(buffered)` / `buffer_thinking(buffered)` - a judged-pass reasoning
          block, emitted now (before any tool) or queued in upstream order among the tools
        - `record_dropped_thinking(judge_failed)` - a reasoning block was removed; blocks the turn's tools
        - `passthrough_start(event)` / `passthrough_delta(event)` / `passthrough_stop(event)` —
          re-emit a non-buffered block type (text, thinking) with a rewritten index
        - `note_judge_unavailable(text)` — request a warning text block at finalize
        - `set_fallback_text(text)` — text to emit if nothing else gets committed
    2. Use returned events directly; they're already wire-ordered for events
       that can commit immediately.
    3. At end of stream, call `finalize(message_delta)` to flush queued
       warnings, marker, buffered tools, and the corrected message_delta.
       Alternatively, for non-streaming use, call
       `to_anthropic_response(template)` after all decisions have been
       recorded.

    The builder is stateful and per-request; do not share across requests.
    """

    def __init__(self) -> None:
        """Create a fresh builder with empty state."""
        self._state = _BuilderState()

    # ------------------------------------------------------------------
    # Upstream buffering (deltas accumulate here until the caller is ready
    # to act on a complete block — typically by calling a judge).
    # ------------------------------------------------------------------

    def begin_text_buffer(self, index: int) -> None:
        """Record that an upstream text block is starting at this index.

        Caller should suppress emission of the block_start; the builder
        re-creates start+delta+stop together at commit time so empty text
        blocks (rejected by Anthropic on next turn) are dropped cleanly.
        """
        self._state.text_buffer[index] = _BufferedTextBlock()

    def append_text_delta(self, index: int, text: str) -> bool:
        """Append a text delta to the buffer at this index.

        Returns True if the delta was buffered, False if no text buffer
        exists at this index (caller should treat as passthrough).
        """
        buffered = self._state.text_buffer.get(index)
        if buffered is None:
            return False
        buffered.text += text
        return True

    def take_text(self, index: int) -> str | None:
        """Pop the buffered text at this index (caller should now decide what to do with it).

        Returns None if no text was buffered at this index.
        """
        buffered = self._state.text_buffer.pop(index, None)
        return buffered.text if buffered is not None else None

    def begin_thinking_buffer(self, index: int, start_event: RawContentBlockStartEvent) -> None:
        """Hold a thinking block back from the wire until it is judged.

        Caller suppresses the start event; the original start and deltas are
        replayed at commit time so the signature survives.
        """
        self._state.thinking_buffer[index] = BufferedThinking(start_event=start_event)

    def append_thinking_delta(self, index: int, event: RawContentBlockDeltaEvent) -> bool:
        """Buffer a thinking/signature delta at this index.

        Returns True if buffered, False if no thinking buffer exists there
        (caller should treat as passthrough).
        """
        buffered = self._state.thinking_buffer.get(index)
        if buffered is None:
            return False
        buffered.deltas.append(event)
        if isinstance(event.delta, ThinkingDelta):
            buffered.text += event.delta.thinking
        return True

    def take_thinking(self, index: int) -> "BufferedThinking | None":
        """Pop the buffered thinking block at this index."""
        return self._state.thinking_buffer.pop(index, None)

    def commit_thinking(self, buffered: "BufferedThinking") -> list[MessageStreamEvent]:
        """Re-emit a judged-pass thinking block now, verbatim, with a rewritten index."""
        self._state.committed_descriptors.append(_descriptor_from_thinking(buffered))
        return self._emit_thinking_now(buffered)

    def buffer_thinking(self, buffered: "BufferedThinking", *, as_context: bool = True) -> None:
        """Queue a reasoning block that upstream sent after a tool_use.

        It is emitted at finalize in its upstream position among the buffered
        tools, so interleaved thinking keeps the order the next turn expects.
        With ``as_context=False`` later judges see the block but not its text.
        """
        self._state.tail.append(buffered)
        descriptor = _descriptor_from_thinking(buffered)
        if not as_context:
            descriptor = BlockDescriptor(type=descriptor.type, content="")
        self._state.committed_descriptors.append(descriptor)

    def commit_reasoning_block(self, block: "AnthropicContentBlock", *, as_context: bool = True) -> None:
        """Non-streaming sibling of `commit_thinking` / `buffer_thinking`.

        A `thinking` or `redacted_thinking` block goes to the pre-tool slot,
        or after the tools already buffered if upstream sent it after one.
        With ``as_context=False`` later judges see the block but not its text.
        """
        if self.has_buffered_tool:
            self._state.tail.append(block)
        else:
            self._state.pre_tool_blocks.append(block)
        thinking = block.get("thinking") if as_context and isinstance(block, dict) else None
        self._state.committed_descriptors.append(
            BlockDescriptor(type=str(block.get("type", "thinking")), content=str(thinking or ""))
        )

    def record_dropped_thinking(self, *, judge_failed: bool = False) -> None:
        """Record that a thinking block was removed by policy.

        Surfaces as a marker at finalize. Because a tool_use must follow its
        thinking when thinking is enabled, the turn's tool calls are blocked too.
        """
        self._state.dropped_thinking += 1
        self._state.dropped_thinking_judge_failed |= judge_failed

    def begin_tool_buffer(self, index: int, *, id: str, name: str) -> None:
        """Record that an upstream tool_use block is starting at this index."""
        self._state.tool_buffer[index] = BufferedTool(id=id, name=name)

    def append_tool_delta(self, index: int, partial_json: str) -> bool:
        """Append an input_json delta to the tool buffer at this index.

        Returns True if buffered, False if no tool buffer exists at this index.
        """
        buffered = self._state.tool_buffer.get(index)
        if buffered is None:
            return False
        buffered.input_json += partial_json
        return True

    def take_tool(self, index: int) -> BufferedTool | None:
        """Pop the buffered tool at this index."""
        return self._state.tool_buffer.pop(index, None)

    @property
    def committed_descriptors(self) -> tuple[BlockDescriptor, ...]:
        """Descriptors for every block the builder has been told about, in commit order.

        Use as the `previous_blocks` list when calling a judge. Includes
        buffered tools (logically committed even if not yet on wire) and
        pending warning/marker/text blocks (so the judge sees them as context).
        """
        return tuple(self._state.committed_descriptors)

    @property
    def has_buffered_tool(self) -> bool:
        """True once at least one tool_use has been buffered for the wire flush."""
        return any(isinstance(item, _QueuedTool) for item in self._state.tail)

    @property
    def has_emitted_anything(self) -> bool:
        """True once any block has been registered (text, tool, passthrough, blocked record).

        Used by callers to decide whether to set a fallback message.
        """
        s = self._state
        return bool(s.committed_descriptors or s.tail or s.blocked_tools or s.pending_warning_text)

    # ------------------------------------------------------------------
    # Commit methods
    # ------------------------------------------------------------------

    def commit_text(self, text: str) -> list[MessageStreamEvent]:
        """Commit a text block. Empty text is suppressed (Anthropic rejects empty text blocks).

        Emits immediately. Callers must not commit text after `buffer_tool`;
        the wire invariant (`tool_use` is the last block) is honored by
        caller discipline, not by builder enforcement.
        """
        if not text:
            return []
        return self._emit_text_now(text)

    def buffer_tool(self, *, id: str, name: str, input_json: str) -> None:
        """Buffer a tool_use block for emission at finalize."""
        self._state.tail.append(_QueuedTool(id=id, name=name, input_json=input_json))
        self._state.committed_descriptors.append(_descriptor_from_tool(name, input_json))

    def record_blocked_tool(self, name: str, *, judge_failed: bool = False) -> None:
        """Record that a tool was blocked. Surfaces as a consolidated marker at finalize."""
        self._state.blocked_tools.append(_BlockedToolRecord(name=name, judge_failed=judge_failed))

    def note_judge_unavailable(self, text: str) -> None:
        """Queue a judge-unavailable warning text block for emission at finalize.

        Last call wins: a subsequent call overwrites the queued text. The
        warning emits before any buffered tools regardless of when this is
        called relative to the tool stream.
        """
        self._state.pending_warning_text = text

    def set_fallback_text(self, text: str) -> None:
        """Set a fallback text block to emit at finalize *only* if nothing else gets committed.

        Used for the on_error=block "judge errored, response is empty" path.
        """
        self._state.pending_fallback_text = text

    def commit_raw_block(self, block: "AnthropicContentBlock") -> None:
        """Commit an opaque content block (e.g. thinking) to the pre-tool slot.

        Used by the non-streaming path for block types the builder has no
        specific handling for. The block is preserved verbatim. Like
        `commit_text`, this lands in the pre-tool region — blocks committed
        after a tool has been buffered are still placed before the tool flush.
        """
        self._state.pre_tool_blocks.append(block)
        block_type = (
            block.get("type") if isinstance(block, dict) else getattr(block, "type", "passthrough")
        ) or "passthrough"
        self._state.committed_descriptors.append(BlockDescriptor(type=str(block_type), content=""))

    def passthrough_start(self, event: RawContentBlockStartEvent) -> list[MessageStreamEvent]:
        """Re-emit a non-buffered content_block_start (text, thinking, etc.) with rewritten index.

        Emits immediately. Callers must not pass non-tool blocks through
        after `buffer_tool`; the upstream invariant ("`tool_use` is the
        last content block") makes that an upstream error, not a case the
        builder defends against.
        """
        index = self._allocate_index()
        self._state.passthrough_index_map[event.index] = index
        block_type = getattr(event.content_block, "type", "passthrough") or "passthrough"
        self._state.committed_descriptors.append(BlockDescriptor(type=block_type, content=""))
        rewritten = event.model_copy(update={"index": index})
        return [cast(MessageStreamEvent, rewritten)]

    def passthrough_delta(self, event: RawContentBlockDeltaEvent) -> list[MessageStreamEvent]:
        """Re-emit a delta for a passthrough block with the rewritten index."""
        idx = self._state.passthrough_index_map.get(event.index)
        if idx is None:
            return []
        return [cast(MessageStreamEvent, event.model_copy(update={"index": idx}))]

    def passthrough_stop(self, event: RawContentBlockStopEvent) -> list[MessageStreamEvent]:
        """Re-emit a stop for a passthrough block with the rewritten index."""
        idx = self._state.passthrough_index_map.get(event.index)
        if idx is None:
            return []
        return [cast(MessageStreamEvent, event.model_copy(update={"index": idx}))]

    # ------------------------------------------------------------------
    # Streaming dispatch (helper for the common tool-buffering pattern).
    # ------------------------------------------------------------------

    async def dispatch_tool_only(
        self,
        event: MessageStreamEvent,
        on_tool_stop: "ToolStopHandler",
    ) -> list[MessageStreamEvent]:
        """Default event router for policies that only inspect tool_use blocks.

        Buffers tool_use deltas internally; non-tool events stream through
        via `passthrough_*`. At each tool block_stop the supplied
        `on_tool_stop` is awaited with the assembled `BufferedTool` — it
        should drive `commit_text` / `buffer_tool` / `record_blocked_tool`
        as appropriate and return any events those calls emitted. At
        message_delta the builder finalizes.

        For richer per-block judging (e.g. SimpleLLMPolicy, which inspects
        text blocks too), call the builder primitives directly instead of
        this helper.
        """
        if isinstance(event, RawContentBlockStartEvent):
            cb = event.content_block
            if isinstance(cb, ToolUseBlock):
                self.begin_tool_buffer(event.index, id=cb.id, name=cb.name)
                return []
            return self.passthrough_start(event)

        if isinstance(event, RawContentBlockDeltaEvent):
            if isinstance(event.delta, InputJSONDelta) and self.append_tool_delta(
                event.index, event.delta.partial_json
            ):
                return []
            return self.passthrough_delta(event)

        if isinstance(event, RawContentBlockStopEvent):
            tool = self.take_tool(event.index)
            if tool is not None:
                return await on_tool_stop(self, tool)
            return self.passthrough_stop(event)

        if isinstance(event, RawMessageDeltaEvent):
            return self.finalize(event)

        return [event]

    # ------------------------------------------------------------------
    # Finalize (streaming)
    # ------------------------------------------------------------------

    def finalize(self, message_delta: RawMessageDeltaEvent) -> list[MessageStreamEvent]:
        """Flush pending warning, blocked-tool marker, buffered tools, and corrected message_delta.

        Order: judge-unavailable warning → dropped-thinking marker →
        blocked-tools marker → buffered tools (with any reasoning queued among
        them) → message_delta. Warning + markers land before the first tool,
        satisfying the trailing-tool_use invariant. Queued reasoning with no
        tool after it goes before the markers instead.
        """
        if self._state.finalized:
            raise RuntimeError("AnthropicMessageBuilder.finalize called twice")
        self._state.finalized = True

        events: list[MessageStreamEvent] = []
        s = self._state
        self._block_tools_after_dropped_thinking()

        # Fallback: if nothing else will be emitted, surface the fallback text.
        nothing_emitted = not self.has_emitted_anything
        if nothing_emitted and s.pending_fallback_text is not None:
            events.extend(self._emit_text_now(s.pending_fallback_text))

        before_markers, after_markers = self._split_tail()
        events.extend(self._emit_items(before_markers))
        for marker in self._pending_markers():
            events.extend(self._emit_text_now(marker))
        events.extend(self._emit_items(after_markers))

        # Conservative stop_reason correction: only rewrite when upstream is
        # known wrong given what we actually emitted. Preserve max_tokens,
        # stop_sequence, refusal, pause_turn, and anything else — clients
        # depend on those signals.
        if message_delta.delta.stop_reason == "tool_use" and not self.has_buffered_tool:
            message_delta = RawMessageDeltaEvent.model_construct(
                type="message_delta",
                delta=message_delta.delta.model_copy(update={"stop_reason": "end_turn"}),
                usage=message_delta.usage,
            )

        events.append(cast(MessageStreamEvent, message_delta))
        return events

    # ------------------------------------------------------------------
    # Finalize (non-streaming)
    # ------------------------------------------------------------------

    def to_anthropic_response(self, template: "AnthropicResponse") -> "AnthropicResponse":
        """Return a finalized non-streaming response composed from builder state.

        Uses `template` as the response shell (id, model, role, usage, etc.)
        and replaces `content` and `stop_reason` with the wire-correct
        composition: pre-tool blocks (in `commit_text`/`passthrough` order)
        → warning → marker → buffered tools. `stop_reason` is corrected to
        match the final shape.

        Should be called after all decisions have been recorded. Callers
        that interleave `to_anthropic_response()` with `finalize()` on the
        same builder will get undefined behavior.
        """
        if self._state.finalized:
            raise RuntimeError("AnthropicMessageBuilder.to_anthropic_response called after finalize")
        self._state.finalized = True

        s = self._state
        self._block_tools_after_dropped_thinking()
        new_content: list["AnthropicContentBlock"] = list(s.pre_tool_blocks)

        if not self.has_emitted_anything and s.pending_fallback_text is not None:
            new_content.append({"type": "text", "text": s.pending_fallback_text})

        before_markers, after_markers = self._split_tail()
        new_content.extend(_content_blocks(before_markers))
        for marker in self._pending_markers():
            new_content.append({"type": "text", "text": marker})
        new_content.extend(_content_blocks(after_markers))

        # Conservative stop_reason correction (see finalize() for rationale):
        # only rewrite the one upstream value known to be wrong given what we
        # emitted. Preserve everything else.
        modified = cast("AnthropicResponse", dict(template))
        modified["content"] = new_content
        upstream_stop = modified.get("stop_reason")
        if upstream_stop == "tool_use" and not self.has_buffered_tool:
            modified["stop_reason"] = "end_turn"
        return modified

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _allocate_index(self) -> int:
        idx = self._state.next_output_index
        self._state.next_output_index += 1
        return idx

    def _emit_text_now(self, text: str) -> list[MessageStreamEvent]:
        index = self._allocate_index()
        self._state.committed_descriptors.append(BlockDescriptor(type="text", content=text))
        self._state.pre_tool_blocks.append({"type": "text", "text": text})
        return _events_for_text(index, text)

    def _block_tools_after_dropped_thinking(self) -> None:
        """Turn the queued tool calls into blocked records once any thinking was dropped.

        With thinking enabled the API rejects a tool_use whose thinking is
        missing on the next turn, and a fake ``redacted_thinking`` can't be
        made, so the tool part of the turn fails instead. Reasoning queued
        among those tools goes with them.
        """
        s = self._state
        if not s.dropped_thinking or not self.has_buffered_tool:
            return
        for item in s.tail:
            if isinstance(item, _QueuedTool):
                s.blocked_tools.append(_BlockedToolRecord(name=item.name, judge_failed=s.dropped_thinking_judge_failed))
            else:
                s.dropped_thinking += 1
        s.tail.clear()

    def _split_tail(self) -> tuple[list["_TailItem"], list["_TailItem"]]:
        """Split the queue at the last tool: reasoning after it goes before the markers.

        Reasoning only keeps its queued position while a tool follows it, so
        the message never ends in a reasoning block after a tool_use.
        """
        tail = self._state.tail
        last_tool = max((i for i, item in enumerate(tail) if isinstance(item, _QueuedTool)), default=-1)
        return tail[last_tool + 1 :], tail[: last_tool + 1]

    def _emit_items(self, items: list["_TailItem"]) -> list[MessageStreamEvent]:
        events: list[MessageStreamEvent] = []
        for item in items:
            if isinstance(item, _QueuedTool):
                events.extend(self._emit_tool_now(item))
            elif isinstance(item, BufferedThinking):
                events.extend(self._emit_thinking_now(item))
        return events

    def _pending_markers(self) -> list[str]:
        s = self._state
        markers: list[str] = []
        if s.pending_warning_text is not None:
            markers.append(s.pending_warning_text)
        if s.dropped_thinking:
            markers.append(dropped_thinking_message(s.dropped_thinking, judge_failed=s.dropped_thinking_judge_failed))
        if s.blocked_tools:
            markers.append(_blocked_tools_marker(s.blocked_tools))
        return markers

    def _emit_thinking_now(self, buffered: "BufferedThinking") -> list[MessageStreamEvent]:
        index = self._allocate_index()
        events: list[MessageStreamEvent] = [
            cast(MessageStreamEvent, buffered.start_event.model_copy(update={"index": index}))
        ]
        events.extend(cast(MessageStreamEvent, d.model_copy(update={"index": index})) for d in buffered.deltas)
        events.append(cast(MessageStreamEvent, RawContentBlockStopEvent(type="content_block_stop", index=index)))
        return events

    def _emit_tool_now(self, tool: _QueuedTool) -> list[MessageStreamEvent]:
        index = self._allocate_index()
        return _events_for_tool_use(
            index,
            tool_id=tool.id,
            name=tool.name,
            input_json=tool.input_json,
        )


# ----------------------------------------------------------------------
# Module-level helpers (also used by the non-streaming path)
# ----------------------------------------------------------------------


def _descriptor_from_tool(name: str, input_json: str) -> BlockDescriptor:
    payload = input_json or "{}"
    return BlockDescriptor(type="tool_use", content=f"{name}({payload})")


def _descriptor_from_thinking(buffered: "BufferedThinking") -> BlockDescriptor:
    block_type = getattr(buffered.start_event.content_block, "type", "thinking") or "thinking"
    return BlockDescriptor(type=block_type, content=buffered.text)


def _events_for_text(index: int, text: str) -> list[MessageStreamEvent]:
    start = RawContentBlockStartEvent(
        type="content_block_start",
        index=index,
        content_block=TextBlock(type="text", text=""),
    )
    delta = RawContentBlockDeltaEvent.model_construct(
        type="content_block_delta",
        index=index,
        delta=TextDelta.model_construct(type="text_delta", text=text),
    )
    stop = RawContentBlockStopEvent(type="content_block_stop", index=index)
    return [cast(MessageStreamEvent, start), cast(MessageStreamEvent, delta), cast(MessageStreamEvent, stop)]


def _events_for_tool_use(index: int, *, tool_id: str, name: str, input_json: str) -> list[MessageStreamEvent]:
    start = RawContentBlockStartEvent(
        type="content_block_start",
        index=index,
        content_block=ToolUseBlock(type="tool_use", id=tool_id, name=name, input={}),
    )
    payload = input_json or "{}"
    delta = RawContentBlockDeltaEvent(
        type="content_block_delta",
        index=index,
        delta=InputJSONDelta(type="input_json_delta", partial_json=payload),
    )
    stop = RawContentBlockStopEvent(type="content_block_stop", index=index)
    return [cast(MessageStreamEvent, start), cast(MessageStreamEvent, delta), cast(MessageStreamEvent, stop)]


def blocked_tools_message(names: list[str]) -> str:
    """Marker text for tools blocked by explicit policy decision."""
    quoted = ", ".join(f"`{n}`" for n in names)
    if len(names) == 1:
        return f"[Tool call {quoted} was blocked by policy]"
    return f"[Tool calls {quoted} were blocked by policy]"


def dropped_thinking_message(count: int, *, judge_failed: bool = False) -> str:
    """Marker text for reasoning blocks removed by policy or because the judge couldn't evaluate them."""
    blocks = "A reasoning block was" if count == 1 else f"{count} reasoning blocks were"
    if judge_failed:
        return f"[{blocks} removed: policy evaluation unavailable]"
    return f"[{blocks} removed by policy]"


def _content_blocks(items: list["_TailItem"]) -> list["AnthropicContentBlock"]:
    blocks: list["AnthropicContentBlock"] = []
    for item in items:
        if isinstance(item, _QueuedTool):
            blocks.append(
                {
                    "type": "tool_use",
                    "id": item.id,
                    "name": item.name,
                    "input": parse_tool_input(item.input_json),
                }
            )
        elif isinstance(item, dict):
            blocks.append(item)
    return blocks


def blocked_tools_judge_failed_message(names: list[str]) -> str:
    """Marker text for tools blocked because the judge couldn't evaluate."""
    quoted = ", ".join(f"`{n}`" for n in names)
    if len(names) == 1:
        return f"[Tool call {quoted} blocked: policy evaluation unavailable]"
    return f"[Tool calls {quoted} blocked: policy evaluation unavailable]"


def _blocked_tools_marker(blocked: list[_BlockedToolRecord]) -> str:
    judge_failed = any(b.judge_failed for b in blocked)
    names = [b.name for b in blocked]
    return blocked_tools_judge_failed_message(names) if judge_failed else blocked_tools_message(names)


def parse_tool_input(input_json: str) -> "JSONObject":
    """Parse buffered tool input JSON, returning the malformed-passthrough sentinel on failure."""
    if not input_json:
        return cast("JSONObject", {})
    try:
        parsed = json.loads(input_json)
    except json.JSONDecodeError:
        return cast("JSONObject", {"_raw": input_json})
    if not isinstance(parsed, dict):
        return cast("JSONObject", {"_raw": input_json})
    return cast("JSONObject", parsed)


__all__ = [
    "AnthropicMessageBuilder",
    "BufferedThinking",
    "BufferedTool",
    "ToolBlockHandler",
    "ToolStopHandler",
    "blocked_tools_message",
    "blocked_tools_judge_failed_message",
    "dropped_thinking_message",
    "compose_tool_only_response",
    "parse_tool_input",
]
