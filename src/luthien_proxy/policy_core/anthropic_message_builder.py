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

Other invariants the builder owns:

- Indices are assigned monotonically by the builder; callers never compute
  them. The builder maintains an upstream→downstream map for passthrough
  blocks so deltas/stops with the same upstream index land on the right
  downstream slot.
- The judge-unavailable warning and the consolidated blocked-tool marker
  emit at `finalize()` in the pre-tool slot, so they can never violate the
  trailing-tool_use invariant regardless of when they were noted.
- `stop_reason` is rewritten at `finalize()` / `to_anthropic_response()`
  to match whether any tool_use was actually emitted.
"""

from __future__ import annotations

import json
import logging
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

from luthien_proxy.policy_core.block_descriptor import BlockDescriptor

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicContentBlock,
        AnthropicResponse,
        JSONObject,
    )

logger = logging.getLogger(__name__)


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


@dataclass
class _BuilderState:
    # Upstream buffering: deltas accumulate here before being judged.
    text_buffer: dict[int, _BufferedTextBlock] = field(default_factory=dict)
    tool_buffer: dict[int, BufferedTool] = field(default_factory=dict)
    # Downstream composition: where committed/queued content lives until flush.
    committed_descriptors: list[BlockDescriptor] = field(default_factory=list)
    pre_tool_blocks: list["AnthropicContentBlock"] = field(default_factory=list)
    buffered_tools: list[_QueuedTool] = field(default_factory=list)
    blocked_tools: list[_BlockedToolRecord] = field(default_factory=list)
    pending_text: list[str] = field(default_factory=list)
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
        return bool(self._state.buffered_tools)

    @property
    def has_emitted_anything(self) -> bool:
        """True once any block has been registered (text, tool, passthrough, blocked record).

        Used by callers to decide whether to set a fallback message.
        """
        s = self._state
        return bool(
            s.committed_descriptors or s.buffered_tools or s.blocked_tools or s.pending_text or s.pending_warning_text
        )

    # ------------------------------------------------------------------
    # Commit methods
    # ------------------------------------------------------------------

    def commit_text(self, text: str) -> list[MessageStreamEvent]:
        """Commit a text block. Empty text is suppressed (Anthropic rejects empty text blocks).

        Emits immediately when no tool_use has been buffered yet. Once a
        tool_use is buffered, text is queued for emission at finalize, in
        the pre-tool slot — keeping the wire-ordering invariant.
        """
        if not text:
            return []
        if self._state.buffered_tools:
            self._state.pending_text.append(text)
            self._state.committed_descriptors.append(BlockDescriptor(type="text", content=text))
            self._state.pre_tool_blocks.append({"type": "text", "text": text})
            return []
        return self._emit_text_now(text)

    def buffer_tool(self, *, id: str, name: str, input_json: str) -> None:
        """Buffer a tool_use block for emission at finalize."""
        self._state.buffered_tools.append(_QueuedTool(id=id, name=name, input_json=input_json))
        self._state.committed_descriptors.append(_descriptor_from_tool(name, input_json))

    def record_blocked_tool(self, name: str, *, judge_failed: bool = False) -> None:
        """Record that a tool was blocked. Surfaces as a consolidated marker at finalize."""
        self._state.blocked_tools.append(_BlockedToolRecord(name=name, judge_failed=judge_failed))

    def note_judge_unavailable(self, text: str) -> None:
        """Queue a judge-unavailable warning text block for emission at finalize.

        Idempotent: subsequent calls overwrite the queued text. The warning
        emits in the pre-tool slot regardless of when this is called relative
        to the tool stream.
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

        Like `commit_text`: emits now if no tool buffered, otherwise the
        passthrough block is dropped — there's no clean way to interleave a
        non-tool block between the pre-tool region and the tool flush. The
        intentional drop preserves the trailing-tool_use invariant (#708).
        """
        if self._state.buffered_tools:
            logger.warning(
                "AnthropicMessageBuilder: dropping passthrough %r at upstream index %d "
                "(arrived after a tool_use was buffered)",
                getattr(event.content_block, "type", "?"),
                event.index,
            )
            return []
        index = self._allocate_index()
        self._state.passthrough_index_map[event.index] = index
        # Track as a passthrough descriptor; type may be "text", "thinking", etc.
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
    # Finalize (streaming)
    # ------------------------------------------------------------------

    def finalize(self, message_delta: RawMessageDeltaEvent) -> list[MessageStreamEvent]:
        """Flush pending warnings, blocked-tool marker, buffered tools, and corrected message_delta.

        Order: pending text (any text that arrived after a tool was buffered)
        → judge-unavailable warning → blocked-tools marker → buffered tools
        → message_delta. The pending text + warning + marker land before the
        first tool, satisfying the trailing-tool_use invariant.
        """
        if self._state.finalized:
            raise RuntimeError("AnthropicMessageBuilder.finalize called twice")
        self._state.finalized = True

        events: list[MessageStreamEvent] = []
        s = self._state

        # Fallback: if nothing else will be emitted, surface the fallback text.
        nothing_emitted = not (s.committed_descriptors or s.buffered_tools or s.blocked_tools or s.pending_warning_text)
        if nothing_emitted and s.pending_fallback_text is not None:
            events.extend(self._emit_text_now(s.pending_fallback_text))

        # Pending text (from text decisions that arrived post-tool-buffer).
        for text in s.pending_text:
            events.extend(_events_for_text(self._allocate_index(), text))
        s.pending_text.clear()

        if s.pending_warning_text is not None:
            events.extend(self._emit_text_now(s.pending_warning_text))

        if s.blocked_tools:
            marker = _blocked_tools_marker(s.blocked_tools)
            events.extend(self._emit_text_now(marker))

        for tool in s.buffered_tools:
            events.extend(self._emit_tool_now(tool))

        # Adjust stop_reason based on what was actually emitted.
        any_tool = bool(s.buffered_tools)
        expected_stop = "tool_use" if any_tool else "end_turn"
        if message_delta.delta.stop_reason != expected_stop:
            message_delta = RawMessageDeltaEvent.model_construct(
                type="message_delta",
                delta=message_delta.delta.model_copy(update={"stop_reason": expected_stop}),
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
        new_content: list["AnthropicContentBlock"] = list(s.pre_tool_blocks)

        if s.pending_warning_text is not None:
            new_content.append({"type": "text", "text": s.pending_warning_text})

        if s.blocked_tools:
            marker = _blocked_tools_marker(s.blocked_tools)
            new_content.append({"type": "text", "text": marker})

        for tool in s.buffered_tools:
            new_content.append(
                {
                    "type": "tool_use",
                    "id": tool.id,
                    "name": tool.name,
                    "input": parse_tool_input(tool.input_json),
                }
            )

        if not new_content and s.pending_fallback_text is not None:
            new_content.append({"type": "text", "text": s.pending_fallback_text})

        expected_stop = "tool_use" if s.buffered_tools else "end_turn"

        modified = cast("AnthropicResponse", dict(template))
        modified["content"] = new_content
        modified["stop_reason"] = expected_stop
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
    "BufferedTool",
    "blocked_tools_message",
    "blocked_tools_judge_failed_message",
    "parse_tool_input",
]
