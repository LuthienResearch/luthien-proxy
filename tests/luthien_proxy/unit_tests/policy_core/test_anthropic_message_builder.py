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


def _text_deltas(events) -> list[str]:
    return [e.delta.text for e in events if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, TextDelta)]


# ----------------------------------------------------------------------
# Module-level helpers
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "input_json,expected",
    [
        ("", {}),
        ('{"a": 1}', {"a": 1}),
        ('{"command":', {"_raw": '{"command":'}),  # malformed JSON
        ("[1, 2, 3]", {"_raw": "[1, 2, 3]"}),  # valid JSON but not a dict
    ],
)
def test_parse_tool_input(input_json: str, expected: dict):
    assert parse_tool_input(input_json) == expected


def test_buffered_tool_parsed_input_delegates_to_parse_tool_input():
    tool = BufferedTool(id="t1", name="Bash", input_json='{"command": "ls"}')
    assert tool.parsed_input == {"command": "ls"}


@pytest.mark.parametrize(
    "helper,names,expected",
    [
        (blocked_tools_message, ["Bash"], "[Tool call `Bash` was blocked by policy]"),
        (blocked_tools_message, ["Bash", "Read"], "[Tool calls `Bash`, `Read` were blocked by policy]"),
        (
            blocked_tools_judge_failed_message,
            ["Bash"],
            "[Tool call `Bash` blocked: policy evaluation unavailable]",
        ),
        (
            blocked_tools_judge_failed_message,
            ["Bash", "Read"],
            "[Tool calls `Bash`, `Read` blocked: policy evaluation unavailable]",
        ),
    ],
)
def test_marker_helpers(helper, names, expected):
    assert helper(names) == expected


# ----------------------------------------------------------------------
# Streaming
# ----------------------------------------------------------------------


class TestCommitText:
    def test_emits_immediately_before_tool(self):
        builder = AnthropicMessageBuilder()
        events = builder.commit_text("hello")
        assert len(events) == 3
        assert isinstance(events[0].content_block, TextBlock)
        assert isinstance(events[1].delta, TextDelta)
        assert events[1].delta.text == "hello"

    def test_empty_text_suppressed(self):
        assert AnthropicMessageBuilder().commit_text("") == []


class TestBufferToolAndFinalize:
    def test_buffer_tool_emits_at_finalize(self):
        builder = AnthropicMessageBuilder()
        builder.buffer_tool(id="t1", name="Bash", input_json='{"x":1}')
        events = builder.finalize(_delta("tool_use"))
        assert len(events) == 4  # start + delta + stop + message_delta
        assert isinstance(events[0].content_block, ToolUseBlock)
        assert events[0].content_block.id == "t1"
        assert isinstance(events[1].delta, InputJSONDelta)
        assert events[1].delta.partial_json == '{"x":1}'
        assert isinstance(events[3], RawMessageDeltaEvent)
        assert events[3].delta.stop_reason == "tool_use"

    @pytest.mark.parametrize(
        "upstream_stop,buffered_tool,expected",
        [
            ("tool_use", False, "end_turn"),  # claimed tool, sent none
            ("end_turn", True, "tool_use"),  # claimed end, sent tool
            ("max_tokens", False, "end_turn"),  # builder forces consistency
            ("max_tokens", True, "tool_use"),
        ],
    )
    def test_finalize_corrects_stop_reason(self, upstream_stop: str, buffered_tool: bool, expected: str):
        builder = AnthropicMessageBuilder()
        if buffered_tool:
            builder.buffer_tool(id="t1", name="Bash", input_json="{}")
        else:
            builder.commit_text("hi")
        events = builder.finalize(_delta(upstream_stop))
        delta_events = [e for e in events if isinstance(e, RawMessageDeltaEvent)]
        assert delta_events[0].delta.stop_reason == expected

    def test_finalize_twice_raises(self):
        builder = AnthropicMessageBuilder()
        builder.finalize(_delta())
        with pytest.raises(RuntimeError, match="finalize called twice"):
            builder.finalize(_delta())


class TestBlockedToolMarker:
    @pytest.mark.parametrize(
        "blocked,expected_substr",
        [
            ([("Bash", False)], "[Tool call `Bash` was blocked by policy]"),
            ([("Bash", False), ("Read", False)], "Bash"),
            ([("Bash", True)], "policy evaluation unavailable"),
            ([("Bash", False), ("Read", True)], "policy evaluation unavailable"),  # any judge_failed dominates
        ],
    )
    def test_marker_text(self, blocked: list[tuple[str, bool]], expected_substr: str):
        builder = AnthropicMessageBuilder()
        for name, judge_failed in blocked:
            builder.record_blocked_tool(name, judge_failed=judge_failed)
        events = builder.finalize(_delta("tool_use"))
        texts = _text_deltas(events)
        assert len(texts) == 1
        assert expected_substr in texts[0]
        # Every blocked name appears in the consolidated marker.
        for name, _ in blocked:
            assert name in texts[0]


class TestWarningAndFallback:
    def test_warning_overwrite_semantics(self):
        """note_judge_unavailable is idempotent: later calls overwrite earlier ones."""
        builder = AnthropicMessageBuilder()
        builder.note_judge_unavailable("first")
        builder.note_judge_unavailable("second")
        texts = _text_deltas(builder.finalize(_delta()))
        assert "second" in texts
        assert "first" not in texts

    def test_fallback_emits_only_when_nothing_else(self):
        builder = AnthropicMessageBuilder()
        builder.set_fallback_text("fallback")
        texts = _text_deltas(builder.finalize(_delta()))
        assert texts == ["fallback"]

    def test_fallback_suppressed_when_other_content(self):
        builder = AnthropicMessageBuilder()
        builder.set_fallback_text("fallback")
        live = builder.commit_text("real")
        final = builder.finalize(_delta())
        assert "fallback" not in _text_deltas(list(live) + list(final))


class TestPassthrough:
    def test_rewrites_index(self):
        builder = AnthropicMessageBuilder()
        upstream = RawContentBlockStartEvent(
            type="content_block_start",
            index=7,
            content_block=TextBlock(type="text", text=""),
        )
        events = builder.passthrough_start(upstream)
        assert len(events) == 1
        assert events[0].index == 0


class TestUpstreamBuffering:
    def test_text_buffer_roundtrip(self):
        builder = AnthropicMessageBuilder()
        builder.begin_text_buffer(0)
        assert builder.append_text_delta(0, "hello ")
        assert builder.append_text_delta(0, "world")
        assert builder.take_text(0) == "hello world"
        # Second take returns None.
        assert builder.take_text(0) is None

    def test_text_delta_for_unbuffered_index_returns_false(self):
        assert AnthropicMessageBuilder().append_text_delta(5, "stray") is False

    def test_tool_buffer_roundtrip(self):
        builder = AnthropicMessageBuilder()
        builder.begin_tool_buffer(0, id="t1", name="Bash")
        assert builder.append_tool_delta(0, '{"command":')
        assert builder.append_tool_delta(0, ' "ls"}')
        tool = builder.take_tool(0)
        assert tool is not None
        assert (tool.id, tool.name, tool.input_json) == ("t1", "Bash", '{"command": "ls"}')


def test_finalize_ordering_warning_marker_tools():
    """Wire order at finalize: warning → marker → tools → message_delta."""
    builder = AnthropicMessageBuilder()
    builder.buffer_tool(id="t1", name="Bash", input_json="{}")
    builder.note_judge_unavailable("WARN")
    builder.record_blocked_tool("Read")

    events = builder.finalize(_delta("tool_use"))
    texts = _text_deltas(events)
    assert texts == ["WARN", "[Tool call `Read` was blocked by policy]"]
    tool_starts = [
        e for e in events if isinstance(e, RawContentBlockStartEvent) and isinstance(e.content_block, ToolUseBlock)
    ]
    text_starts = [
        e for e in events if isinstance(e, RawContentBlockStartEvent) and isinstance(e.content_block, TextBlock)
    ]
    assert events.index(text_starts[-1]) < events.index(tool_starts[0])


# ----------------------------------------------------------------------
# Non-streaming: to_anthropic_response
# ----------------------------------------------------------------------


class TestToAnthropicResponse:
    def test_empty_builder_produces_end_turn(self):
        result = AnthropicMessageBuilder().to_anthropic_response(_response_template())
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

    def test_tool_trails_text_even_when_added_first(self):
        builder = AnthropicMessageBuilder()
        builder.buffer_tool(id="t1", name="Bash", input_json="{}")
        builder.commit_text("after-tool")
        result = builder.to_anthropic_response(_response_template())
        assert [b["type"] for b in result["content"]] == ["text", "tool_use"]
        assert result["content"][0]["text"] == "after-tool"

    def test_blocked_tools_produce_marker(self):
        builder = AnthropicMessageBuilder()
        builder.record_blocked_tool("Bash")
        result = builder.to_anthropic_response(_response_template())
        assert result["content"] == [
            {"type": "text", "text": "[Tool call `Bash` was blocked by policy]"},
        ]
        assert result["stop_reason"] == "end_turn"

    def test_warning_in_pre_tool_slot(self):
        builder = AnthropicMessageBuilder()
        builder.note_judge_unavailable("WARN")
        builder.buffer_tool(id="t1", name="Bash", input_json="{}")
        result = builder.to_anthropic_response(_response_template())
        assert [b["type"] for b in result["content"]] == ["text", "tool_use"]
        assert result["content"][0]["text"] == "WARN"

    def test_fallback_only_when_empty(self):
        builder = AnthropicMessageBuilder()
        builder.set_fallback_text("nothing-else")
        builder.commit_text("real")
        result = builder.to_anthropic_response(_response_template())
        texts = [b["text"] for b in result["content"] if b["type"] == "text"]
        assert texts == ["real"]

    def test_template_envelope_preserved(self):
        builder = AnthropicMessageBuilder()
        builder.commit_text("hi")
        template = _response_template()
        result = builder.to_anthropic_response(template)
        assert (result["id"], result["model"], result["usage"]) == (
            template["id"],
            template["model"],
            template["usage"],
        )

    def test_commit_raw_block_preserved_in_pre_tool_slot(self):
        builder = AnthropicMessageBuilder()
        builder.commit_raw_block(cast(dict, {"type": "thinking", "thinking": "step-by-step"}))  # type: ignore[arg-type]
        builder.buffer_tool(id="t1", name="Bash", input_json="{}")
        result = builder.to_anthropic_response(_response_template())
        assert [b["type"] for b in result["content"]] == ["thinking", "tool_use"]

    def test_to_response_twice_raises(self):
        builder = AnthropicMessageBuilder()
        builder.to_anthropic_response(_response_template())
        with pytest.raises(RuntimeError):
            builder.to_anthropic_response(_response_template())


def test_committed_descriptors_track_commits():
    builder = AnthropicMessageBuilder()
    builder.commit_text("hi")
    builder.buffer_tool(id="t1", name="Bash", input_json='{"x":1}')
    types = [d.type for d in builder.committed_descriptors]
    assert "text" in types
    assert "tool_use" in types
