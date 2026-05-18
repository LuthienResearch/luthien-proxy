"""Unit tests for tool_call_judge_utils module.

Tests for the utility functions used by ToolCallJudgePolicy:
- Judge prompt building
- Judge response parsing (JSON, fenced code blocks, error handling)
"""

from __future__ import annotations

import pytest

from luthien_proxy.policies.tool_call_judge_policy import ToolCallJudgeConfig
from luthien_proxy.policies.tool_call_judge_utils import (
    build_judge_prompt,
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
