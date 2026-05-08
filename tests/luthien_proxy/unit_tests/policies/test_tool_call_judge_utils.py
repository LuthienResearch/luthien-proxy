"""Unit tests for tool_call_judge_utils module.

Tests for the utility functions used by ToolCallJudgePolicy:
- Judge prompt building
- Judge response parsing (JSON, fenced code blocks, error handling)
"""

from __future__ import annotations

import pytest
from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    TextBlock,
    TextDelta,
    ToolUseBlock,
)

from luthien_proxy.policies.tool_call_judge_policy import ToolCallJudgeConfig
from luthien_proxy.policies.tool_call_judge_utils import (
    BufferedToolUse,
    build_allowed_tool_use_events,
    build_blocked_text_events,
    build_judge_prompt,
    handle_tool_use_block_delta,
    handle_tool_use_block_start,
    parse_judge_response,
)


def _make_config(**overrides) -> ToolCallJudgeConfig:
    """Build a config with the now-required auth_provider already set."""
    overrides.setdefault("auth_provider", "user_credentials")
    return ToolCallJudgeConfig(**overrides)


class TestToolCallJudgeConfig:
    """Test config model validation and defaults."""

    def test_auth_provider_required(self):
        """Building a config without auth_provider now raises a validation error."""
        with pytest.raises(Exception):
            ToolCallJudgeConfig()  # type: ignore[call-arg]

    def test_frozen(self):
        config = _make_config()
        with pytest.raises(Exception):
            config.model = "other"  # type: ignore[misc]


class TestBuildJudgePrompt:
    """Test judge prompt building utilities."""

    def test_build_judge_prompt(self):
        """Test that build_judge_prompt creates proper message structure."""
        prompt = build_judge_prompt(
            name="test_tool",
            arguments='{"key": "value"}',
            judge_instructions="You are a security analyst.",
        )

        assert len(prompt) == 2
        assert prompt[0]["role"] == "system"
        assert prompt[0]["content"] == "You are a security analyst."
        assert prompt[1]["role"] == "user"
        assert "test_tool" in prompt[1]["content"]
        assert '{"key": "value"}' in prompt[1]["content"]


class TestParseJudgeResponse:
    """Test judge response parsing utilities."""

    def test_parse_judge_response_plain_json(self):
        """Test parsing plain JSON response."""
        content = '{"probability": 0.8, "explanation": "test"}'
        result = parse_judge_response(content)

        assert result["probability"] == 0.8
        assert result["explanation"] == "test"

    def test_parse_judge_response_fenced_json(self):
        """Test parsing JSON with fenced code block."""
        content = '```json\n{"probability": 0.8, "explanation": "test"}\n```'
        result = parse_judge_response(content)

        assert result["probability"] == 0.8
        assert result["explanation"] == "test"

    def test_parse_judge_response_fenced_no_language(self):
        """Test parsing fenced code block without language specifier."""
        content = '```\n{"probability": 0.8, "explanation": "test"}\n```'
        result = parse_judge_response(content)

        assert result["probability"] == 0.8
        assert result["explanation"] == "test"

    def test_parse_judge_response_fenced_json_multiline_body(self):
        """Fenced ```json block with multi-line JSON body parses correctly.

        Regression: the match set previously included "```json" as a dead entry
        (lstrip("`") already strips all backticks). This test confirms the
        remaining {"json", ""} entries handle the fenced-json case.
        """
        content = '```json\n{\n  "probability": 0.9,\n  "explanation": "multi-line"\n}\n```'
        result = parse_judge_response(content)

        assert result["probability"] == 0.9
        assert result["explanation"] == "multi-line"

    def test_parse_judge_response_invalid_json(self):
        """Test that invalid JSON raises ValueError."""
        with pytest.raises(ValueError, match="JSON parsing failed"):
            parse_judge_response("not json at all")

    def test_parse_judge_response_non_dict(self):
        """Test that non-dict JSON raises ValueError."""
        with pytest.raises(ValueError, match="must be a JSON object"):
            parse_judge_response("[1, 2, 3]")


class TestParseToJudgeResult:
    """Test parse_to_judge_result: probability clamping, prompt attachment, validation."""

    def test_clamps_probability_high(self):
        from luthien_proxy.policies.tool_call_judge_utils import parse_to_judge_result

        result = parse_to_judge_result(
            '{"probability": 1.5, "explanation": "test"}',
            prompt=[{"role": "system", "content": "x"}],
        )
        assert result.probability == 1.0
        assert result.explanation == "test"

    def test_clamps_probability_low(self):
        from luthien_proxy.policies.tool_call_judge_utils import parse_to_judge_result

        result = parse_to_judge_result(
            '{"probability": -0.2, "explanation": "test"}',
            prompt=[],
        )
        assert result.probability == 0.0

    def test_fails_on_missing_probability(self):
        """Security-critical: missing probability must raise, not default to allow."""
        from luthien_proxy.policies.tool_call_judge_utils import parse_to_judge_result

        with pytest.raises(ValueError, match="missing required 'probability' field"):
            parse_to_judge_result(
                '{"explanation": "no probability"}',
                prompt=[],
            )

    def test_attaches_prompt_for_audit(self):
        from luthien_proxy.policies.tool_call_judge_utils import parse_to_judge_result

        prompt = [{"role": "system", "content": "judge prompt"}]
        result = parse_to_judge_result(
            '{"probability": 0.5, "explanation": "x"}',
            prompt=prompt,
        )
        assert result.prompt == prompt


class TestBufferedToolUse:
    def test_defaults(self):
        buf = BufferedToolUse(id="toolu_1", name="Bash")
        assert buf.input_json == ""

    def test_mutable_input_json(self):
        buf = BufferedToolUse(id="toolu_1", name="Bash")
        buf.input_json += '{"cmd":'
        buf.input_json += '"ls"}'
        assert buf.input_json == '{"cmd":"ls"}'


class TestHandleToolUseBlockStart:
    def test_tool_use_block_buffered_and_suppressed(self):
        content_block = ToolUseBlock(type="tool_use", id="toolu_123", name="Bash", input={})
        event = RawContentBlockStartEvent(type="content_block_start", index=2, content_block=content_block)
        buffer: dict = {}
        result = handle_tool_use_block_start(event, buffer)
        assert result == []
        assert 2 in buffer
        assert buffer[2].id == "toolu_123"
        assert buffer[2].name == "Bash"

    def test_non_tool_use_block_passes_through(self):
        content_block = TextBlock(type="text", text="hello")
        event = RawContentBlockStartEvent(type="content_block_start", index=0, content_block=content_block)
        buffer: dict = {}
        result = handle_tool_use_block_start(event, buffer)
        assert len(result) == 1
        assert buffer == {}


class TestHandleToolUseBlockDelta:
    def test_accumulates_json_for_buffered_index(self):
        buffer = {0: BufferedToolUse(id="t", name="Bash")}
        delta = InputJSONDelta(type="input_json_delta", partial_json='{"cmd":')
        event = RawContentBlockDeltaEvent(type="content_block_delta", index=0, delta=delta)
        result = handle_tool_use_block_delta(event, buffer)
        assert result == []
        assert buffer[0].input_json == '{"cmd":'

    def test_non_buffered_index_passes_through(self):
        buffer: dict = {}
        delta = TextDelta(type="text_delta", text="hello")
        event = RawContentBlockDeltaEvent(type="content_block_delta", index=0, delta=delta)
        result = handle_tool_use_block_delta(event, buffer)
        assert len(result) == 1


class TestBuildAllowedToolUseEvents:
    def test_returns_start_delta_stop(self):
        buffered = BufferedToolUse(id="toolu_abc", name="MyTool", input_json='{"x":1}')
        stop_event = RawContentBlockStopEvent(type="content_block_stop", index=1)
        events = build_allowed_tool_use_events(buffered, stop_event)

        assert len(events) == 3
        assert isinstance(events[0], RawContentBlockStartEvent)
        assert isinstance(events[0].content_block, ToolUseBlock)
        assert events[0].content_block.id == "toolu_abc"
        assert events[0].content_block.name == "MyTool"
        assert isinstance(events[1], RawContentBlockDeltaEvent)
        assert isinstance(events[1].delta, InputJSONDelta)
        assert events[1].delta.partial_json == '{"x":1}'
        assert events[2].type == "content_block_stop"

    def test_empty_input_json_becomes_empty_object(self):
        buffered = BufferedToolUse(id="t", name="T", input_json="")
        stop_event = RawContentBlockStopEvent(type="content_block_stop", index=0)
        events = build_allowed_tool_use_events(buffered, stop_event)
        assert isinstance(events[1], RawContentBlockDeltaEvent)
        assert isinstance(events[1].delta, InputJSONDelta)
        assert events[1].delta.partial_json == "{}"


class TestBuildBlockedTextEvents:
    def test_returns_text_start_delta_stop(self):
        stop_event = RawContentBlockStopEvent(type="content_block_stop", index=3)
        events = build_blocked_text_events(3, stop_event, "BLOCKED: dangerous")

        assert len(events) == 3
        assert isinstance(events[0], RawContentBlockStartEvent)
        assert isinstance(events[0].content_block, TextBlock)
        assert events[0].index == 3
        assert isinstance(events[1], RawContentBlockDeltaEvent)
        assert isinstance(events[1].delta, TextDelta)
        assert events[1].delta.text == "BLOCKED: dangerous"
        assert events[2].type == "content_block_stop"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
