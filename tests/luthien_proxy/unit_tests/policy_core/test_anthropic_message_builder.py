"""Unit tests for AnthropicMessageBuilder.

Covers the primitive operations (commit_text, buffer_tool, passthrough,
record_blocked_tool, note_judge_unavailable, set_fallback_text), the
streaming `finalize()` flush, and the non-streaming `to_anthropic_response()`
sibling. The trailing-tool_use invariant (#708) is enforced by the builder
itself; tests assert wire ordering across mixed inputs.
"""

from __future__ import annotations

from typing import cast

import pytest
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
from anthropic.types.raw_message_delta_event import Delta

from luthien_proxy.llm.types.anthropic import AnthropicResponse
from luthien_proxy.policy_core.anthropic_message_builder import (
    AnthropicMessageBuilder,
    BufferedTool,
    blocked_tools_judge_failed_message,
    blocked_tools_message,
    parse_tool_input,
)


def _delta(stop_reason: str = "end_turn") -> RawMessageDeltaEvent:
    return RawMessageDeltaEvent.model_construct(
        type="message_delta",
        delta=Delta.model_construct(stop_reason=stop_reason, stop_sequence=None),
        usage=Usage(input_tokens=1, output_tokens=2),
    )


def _response_template() -> AnthropicResponse:
    return cast(
        AnthropicResponse,
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": "claude-haiku-4-5",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    )


class TestBufferedTool:
    def test_parsed_input_parses_valid_json(self):
        tool = BufferedTool(id="t1", name="Bash", input_json='{"command": "ls"}')
        assert tool.parsed_input == {"command": "ls"}

    def test_parsed_input_empty_returns_empty_dict(self):
        assert BufferedTool(id="t1", name="Bash").parsed_input == {}

    def test_parsed_input_malformed_wraps_in_raw(self):
        tool = BufferedTool(id="t1", name="Bash", input_json='{"command":')
        assert tool.parsed_input == {"_raw": '{"command":'}

    def test_parsed_input_non_dict_wraps_in_raw(self):
        tool = BufferedTool(id="t1", name="Bash", input_json="[1, 2, 3]")
        assert tool.parsed_input == {"_raw": "[1, 2, 3]"}


class TestParseToolInput:
    def test_empty_string_returns_empty_dict(self):
        assert parse_tool_input("") == {}

    def test_valid_json_object(self):
        assert parse_tool_input('{"a": 1}') == {"a": 1}

    def test_malformed_returns_raw_sentinel(self):
        assert parse_tool_input("{not json") == {"_raw": "{not json"}

    def test_non_dict_returns_raw_sentinel(self):
        assert parse_tool_input("[1, 2]") == {"_raw": "[1, 2]"}


class TestMarkerHelpers:
    def test_blocked_tools_message_single(self):
        assert blocked_tools_message(["Bash"]) == "[Tool call `Bash` was blocked by policy]"

    def test_blocked_tools_message_plural(self):
        assert blocked_tools_message(["Bash", "Read"]) == "[Tool calls `Bash`, `Read` were blocked by policy]"

    def test_blocked_tools_judge_failed_message_single(self):
        assert (
            blocked_tools_judge_failed_message(["Bash"]) == "[Tool call `Bash` blocked: policy evaluation unavailable]"
        )

    def test_blocked_tools_judge_failed_message_plural(self):
        assert (
            blocked_tools_judge_failed_message(["Bash", "Read"])
            == "[Tool calls `Bash`, `Read` blocked: policy evaluation unavailable]"
        )


class TestCommitText:
    def test_commit_text_before_tool_emits_immediately(self):
        builder = AnthropicMessageBuilder()
        events = builder.commit_text("hello")
        assert len(events) == 3
        assert isinstance(events[0], RawContentBlockStartEvent)
        assert isinstance(events[0].content_block, TextBlock)
        assert isinstance(events[1], RawContentBlockDeltaEvent)
        assert isinstance(events[1].delta, TextDelta)
        assert events[1].delta.text == "hello"
        assert isinstance(events[2], RawContentBlockStopEvent)

    def test_empty_text_suppressed(self):
        builder = AnthropicMessageBuilder()
        assert builder.commit_text("") == []

    def test_text_after_tool_queues_for_pre_tool_flush(self):
        builder = AnthropicMessageBuilder()
        builder.buffer_tool(id="t1", name="Bash", input_json='{"x": 1}')
        events = builder.commit_text("trailing")
        assert events == []
        # The pending text emits at finalize, before the tool.
        flushed = builder.finalize(_delta("tool_use"))
        types = [type(e).__name__ for e in flushed]
        # text(start,delta,stop), tool(start,delta,stop), message_delta
        text_start = next(e for e in flushed if isinstance(e, RawContentBlockStartEvent))
        assert isinstance(text_start.content_block, TextBlock)
        tool_starts = [
            e for e in flushed if isinstance(e, RawContentBlockStartEvent) and isinstance(e.content_block, ToolUseBlock)
        ]
        assert tool_starts
        # The text must precede the tool on the wire.
        text_index = flushed.index(text_start)
        tool_index = flushed.index(tool_starts[0])
        assert text_index < tool_index, types


class TestBufferToolAndFinalize:
    def test_buffer_tool_emits_at_finalize(self):
        builder = AnthropicMessageBuilder()
        builder.buffer_tool(id="t1", name="Bash", input_json='{"x":1}')
        events = builder.finalize(_delta("tool_use"))
        # start + delta + stop + message_delta
        assert len(events) == 4
        assert isinstance(events[0].content_block, ToolUseBlock)
        assert events[0].content_block.id == "t1"
        assert events[0].content_block.name == "Bash"
        assert isinstance(events[1].delta, InputJSONDelta)
        assert events[1].delta.partial_json == '{"x":1}'
        assert isinstance(events[3], RawMessageDeltaEvent)
        assert events[3].delta.stop_reason == "tool_use"

    def test_finalize_with_no_tools_corrects_stop_reason(self):
        builder = AnthropicMessageBuilder()
        builder.commit_text("hi")
        events = builder.finalize(_delta("tool_use"))
        delta_events = [e for e in events if isinstance(e, RawMessageDeltaEvent)]
        assert len(delta_events) == 1
        assert delta_events[0].delta.stop_reason == "end_turn"

    def test_finalize_twice_raises(self):
        builder = AnthropicMessageBuilder()
        builder.finalize(_delta())
        with pytest.raises(RuntimeError, match="finalize called twice"):
            builder.finalize(_delta())


class TestBlockedToolMarker:
    def test_single_blocked_tool_marker(self):
        builder = AnthropicMessageBuilder()
        builder.record_blocked_tool("Bash")
        events = builder.finalize(_delta("tool_use"))
        text_deltas = [e for e in events if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, TextDelta)]
        assert len(text_deltas) == 1
        assert text_deltas[0].delta.text == "[Tool call `Bash` was blocked by policy]"

    def test_multiple_blocked_tools_consolidated(self):
        builder = AnthropicMessageBuilder()
        builder.record_blocked_tool("Bash")
        builder.record_blocked_tool("Read")
        events = builder.finalize(_delta("tool_use"))
        text_deltas = [e for e in events if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, TextDelta)]
        assert len(text_deltas) == 1
        assert "Bash" in text_deltas[0].delta.text
        assert "Read" in text_deltas[0].delta.text

    def test_judge_failed_marker(self):
        builder = AnthropicMessageBuilder()
        builder.record_blocked_tool("Bash", judge_failed=True)
        events = builder.finalize(_delta("tool_use"))
        text_deltas = [e for e in events if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, TextDelta)]
        assert "policy evaluation unavailable" in text_deltas[0].delta.text

    def test_mixed_judge_failed_dominates(self):
        """If any blocked tool was judge_failed, the consolidated marker uses the judge-failed phrasing."""
        builder = AnthropicMessageBuilder()
        builder.record_blocked_tool("Bash", judge_failed=False)
        builder.record_blocked_tool("Read", judge_failed=True)
        events = builder.finalize(_delta("tool_use"))
        text_deltas = [e for e in events if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, TextDelta)]
        assert "policy evaluation unavailable" in text_deltas[0].delta.text


class TestNoteJudgeUnavailable:
    def test_warning_emits_at_finalize(self):
        builder = AnthropicMessageBuilder()
        builder.note_judge_unavailable("WARN")
        events = builder.finalize(_delta())
        text_deltas = [e for e in events if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, TextDelta)]
        assert any(d.delta.text == "WARN" for d in text_deltas)

    def test_warning_overwrite_semantics(self):
        """Repeated calls overwrite the pending warning rather than appending."""
        builder = AnthropicMessageBuilder()
        builder.note_judge_unavailable("first")
        builder.note_judge_unavailable("second")
        events = builder.finalize(_delta())
        text_deltas = [e for e in events if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, TextDelta)]
        texts = [d.delta.text for d in text_deltas]
        assert "second" in texts
        assert "first" not in texts


class TestFallbackText:
    def test_fallback_emits_when_nothing_else(self):
        builder = AnthropicMessageBuilder()
        builder.set_fallback_text("fallback")
        events = builder.finalize(_delta())
        text_deltas = [e for e in events if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, TextDelta)]
        assert any(d.delta.text == "fallback" for d in text_deltas)

    def test_fallback_suppressed_when_other_content_emits(self):
        builder = AnthropicMessageBuilder()
        builder.set_fallback_text("fallback")
        live_events = builder.commit_text("real content")
        finalize_events = builder.finalize(_delta())
        all_events = list(live_events) + list(finalize_events)
        text_deltas = [
            e for e in all_events if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, TextDelta)
        ]
        texts = [d.delta.text for d in text_deltas]
        assert "real content" in texts
        assert "fallback" not in texts


class TestPassthrough:
    def test_passthrough_rewrites_index(self):
        builder = AnthropicMessageBuilder()
        upstream = RawContentBlockStartEvent(
            type="content_block_start",
            index=7,
            content_block=TextBlock(type="text", text=""),
        )
        events = builder.passthrough_start(upstream)
        assert len(events) == 1
        assert events[0].index == 0

    def test_passthrough_dropped_after_tool_buffered(self):
        builder = AnthropicMessageBuilder()
        builder.buffer_tool(id="t1", name="Bash", input_json="{}")
        upstream = RawContentBlockStartEvent(
            type="content_block_start",
            index=0,
            content_block=TextBlock(type="text", text=""),
        )
        # Passthrough after tool buffered → dropped to preserve trailing-tool invariant.
        assert builder.passthrough_start(upstream) == []


class TestStreamingUpstreamBuffering:
    def test_text_buffer_accumulates_deltas(self):
        builder = AnthropicMessageBuilder()
        builder.begin_text_buffer(0)
        assert builder.append_text_delta(0, "hello ")
        assert builder.append_text_delta(0, "world")
        assert builder.take_text(0) == "hello world"
        # Second take returns None.
        assert builder.take_text(0) is None

    def test_text_delta_for_unbuffered_index_returns_false(self):
        builder = AnthropicMessageBuilder()
        assert builder.append_text_delta(5, "stray") is False

    def test_tool_buffer_accumulates_deltas(self):
        builder = AnthropicMessageBuilder()
        builder.begin_tool_buffer(0, id="t1", name="Bash")
        assert builder.append_tool_delta(0, '{"command":')
        assert builder.append_tool_delta(0, ' "ls"}')
        tool = builder.take_tool(0)
        assert tool is not None
        assert tool.id == "t1"
        assert tool.name == "Bash"
        assert tool.input_json == '{"command": "ls"}'


class TestCommittedDescriptors:
    def test_descriptors_track_text_and_tool(self):
        builder = AnthropicMessageBuilder()
        builder.commit_text("hi")
        builder.buffer_tool(id="t1", name="Bash", input_json='{"x":1}')
        descriptors = builder.committed_descriptors
        types = [d.type for d in descriptors]
        assert "text" in types
        assert "tool_use" in types


class TestToAnthropicResponse:
    def test_empty_builder_produces_end_turn(self):
        builder = AnthropicMessageBuilder()
        result = builder.to_anthropic_response(_response_template())
        assert result["content"] == []
        assert result["stop_reason"] == "end_turn"

    def test_text_only(self):
        builder = AnthropicMessageBuilder()
        builder.commit_text("hello")
        result = builder.to_anthropic_response(_response_template())
        assert result["content"] == [{"type": "text", "text": "hello"}]
        assert result["stop_reason"] == "end_turn"

    def test_tool_only(self):
        builder = AnthropicMessageBuilder()
        builder.buffer_tool(id="t1", name="Bash", input_json='{"command": "ls"}')
        result = builder.to_anthropic_response(_response_template())
        assert result["content"] == [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
        ]
        assert result["stop_reason"] == "tool_use"

    def test_tool_trails_text(self):
        builder = AnthropicMessageBuilder()
        # Even when interleaved at the caller level, tools land last.
        builder.buffer_tool(id="t1", name="Bash", input_json="{}")
        builder.commit_text("after-tool")
        result = builder.to_anthropic_response(_response_template())
        types = [b["type"] for b in result["content"]]
        assert types == ["text", "tool_use"]
        assert result["content"][0]["text"] == "after-tool"

    def test_blocked_tools_produces_marker(self):
        builder = AnthropicMessageBuilder()
        builder.record_blocked_tool("Bash")
        result = builder.to_anthropic_response(_response_template())
        assert result["content"] == [
            {"type": "text", "text": "[Tool call `Bash` was blocked by policy]"},
        ]
        assert result["stop_reason"] == "end_turn"

    def test_judge_unavailable_warning_in_pre_tool_slot(self):
        builder = AnthropicMessageBuilder()
        builder.note_judge_unavailable("WARN")
        builder.buffer_tool(id="t1", name="Bash", input_json="{}")
        result = builder.to_anthropic_response(_response_template())
        types = [b["type"] for b in result["content"]]
        assert types == ["text", "tool_use"]
        assert result["content"][0]["text"] == "WARN"

    def test_fallback_when_empty(self):
        builder = AnthropicMessageBuilder()
        builder.set_fallback_text("nothing-else")
        result = builder.to_anthropic_response(_response_template())
        assert result["content"] == [{"type": "text", "text": "nothing-else"}]

    def test_fallback_suppressed_when_content_present(self):
        builder = AnthropicMessageBuilder()
        builder.set_fallback_text("nothing-else")
        builder.commit_text("real")
        result = builder.to_anthropic_response(_response_template())
        texts = [b["text"] for b in result["content"] if b["type"] == "text"]
        assert "real" in texts
        assert "nothing-else" not in texts

    def test_template_id_and_usage_preserved(self):
        builder = AnthropicMessageBuilder()
        builder.commit_text("hi")
        template = _response_template()
        result = builder.to_anthropic_response(template)
        assert result["id"] == template["id"]
        assert result["usage"] == template["usage"]
        assert result["model"] == template["model"]

    def test_commit_raw_block_preserved_in_pre_tool_slot(self):
        builder = AnthropicMessageBuilder()
        thinking = cast(
            dict,
            {"type": "thinking", "thinking": "step-by-step"},
        )
        builder.commit_raw_block(thinking)  # type: ignore[arg-type]
        builder.buffer_tool(id="t1", name="Bash", input_json="{}")
        result = builder.to_anthropic_response(_response_template())
        types = [b["type"] for b in result["content"]]
        assert types == ["thinking", "tool_use"]

    def test_to_response_twice_raises(self):
        builder = AnthropicMessageBuilder()
        builder.to_anthropic_response(_response_template())
        with pytest.raises(RuntimeError):
            builder.to_anthropic_response(_response_template())


class TestFinalizeOrdering:
    def test_pending_text_before_warning_before_marker_before_tools(self):
        """Wire order: pending pre-tool text → warning → marker → tools → message_delta."""
        builder = AnthropicMessageBuilder()
        builder.buffer_tool(id="t1", name="Bash", input_json="{}")
        # Now commit_text queues as pending_text.
        builder.commit_text("post-tool-text")
        builder.note_judge_unavailable("WARN")
        builder.record_blocked_tool("Read")

        events = builder.finalize(_delta("tool_use"))
        text_blocks = [e for e in events if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, TextDelta)]
        texts = [d.delta.text for d in text_blocks]
        assert texts == ["post-tool-text", "WARN", "[Tool call `Read` was blocked by policy]"]
        # The tool_use start follows all text blocks.
        tool_starts = [
            e for e in events if isinstance(e, RawContentBlockStartEvent) and isinstance(e.content_block, ToolUseBlock)
        ]
        text_starts = [
            e for e in events if isinstance(e, RawContentBlockStartEvent) and isinstance(e.content_block, TextBlock)
        ]
        assert text_starts and tool_starts
        assert events.index(text_starts[-1]) < events.index(tool_starts[0])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
