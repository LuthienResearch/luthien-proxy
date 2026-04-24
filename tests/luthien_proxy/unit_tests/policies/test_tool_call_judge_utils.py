"""Unit tests for tool_call_judge_utils module.

Tests for the utility functions used by ToolCallJudgePolicy:
- Judge prompt building
- Judge response parsing (JSON, fenced code blocks, error handling)
- Probability parsing + clamping
"""

from __future__ import annotations

import pytest

from luthien_proxy.policies.tool_call_judge_policy import ToolCallJudgeConfig
from luthien_proxy.policies.tool_call_judge_utils import (
    JudgeConfig,
    build_judge_prompt,
    parse_judge_response,
    parse_to_judge_result,
)


class TestToolCallJudgeConfig:
    """Test config model validation and defaults."""

    def test_frozen(self):
        config = ToolCallJudgeConfig()
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
        """Fenced ```json block with multi-line JSON body parses correctly."""
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
    """Probability clamping + required-field validation."""

    def test_happy_path(self):
        result = parse_to_judge_result(
            '{"probability": 0.75, "explanation": "somewhat dangerous"}',
            prompt=[{"role": "user", "content": "x"}],
        )
        assert result.probability == 0.75
        assert result.explanation == "somewhat dangerous"

    def test_clamps_above_one(self):
        result = parse_to_judge_result(
            '{"probability": 1.5, "explanation": "test"}',
            prompt=[],
        )
        assert result.probability == 1.0
        assert result.explanation == "test"

    def test_clamps_below_zero(self):
        result = parse_to_judge_result(
            '{"probability": -0.5, "explanation": "test"}',
            prompt=[],
        )
        assert result.probability == 0.0

    def test_fails_on_missing_probability(self):
        """Missing probability → ValueError (fail-secure; callers must block)."""
        with pytest.raises(ValueError, match="missing required 'probability' field"):
            parse_to_judge_result(
                '{"explanation": "test without probability"}',
                prompt=[],
            )

    def test_missing_explanation_defaults_to_empty(self):
        result = parse_to_judge_result(
            '{"probability": 0.4}',
            prompt=[],
        )
        assert result.probability == 0.4
        assert result.explanation == ""


class TestJudgeConfig:
    def test_defaults(self):
        config = JudgeConfig(model="test-model")
        assert config.model == "test-model"
        assert config.api_base is None
        assert config.probability_threshold == 0.6
        assert config.temperature == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
