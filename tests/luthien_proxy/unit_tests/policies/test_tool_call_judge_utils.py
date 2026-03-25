"""Unit tests for tool_call_judge_utils module.

Tests for the utility functions used by ToolCallJudgePolicy:
- Judge prompt building
- Judge response parsing (JSON, fenced code blocks, error handling)
- Judge LLM calling with configuration
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from luthien_proxy.llm.completion import CompletionResult
from luthien_proxy.policies.tool_call_judge_utils import (
    JudgeConfig,
    build_judge_prompt,
    call_judge,
    parse_judge_response,
)


class TestJudgeConfig:
    def test_api_base_alias(self):
        """api_base is accepted as alias for base_url (backwards compat)."""
        config = JudgeConfig(model="test", api_base="http://custom:8080")
        assert config.base_url == "http://custom:8080"


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

    def test_parse_judge_response_invalid_json(self):
        """Test that invalid JSON raises ValueError."""
        with pytest.raises(ValueError, match="JSON parsing failed"):
            parse_judge_response("not json at all")

    def test_parse_judge_response_non_dict(self):
        """Test that non-dict JSON raises ValueError."""
        with pytest.raises(ValueError, match="must be a JSON object"):
            parse_judge_response("[1, 2, 3]")


class TestCallJudge:
    """Test the judge calling functionality."""

    @pytest.mark.asyncio
    async def test_call_judge_success(self):
        """Test successful judge call and result parsing."""
        config = JudgeConfig(
            model="test-model",
            base_url=None,
            api_key=None,
            probability_threshold=0.5,
            temperature=0.0,
            max_tokens=256,
        )

        mock_result = CompletionResult(
            text='{"probability": 0.75, "explanation": "somewhat dangerous"}',
            input_tokens=10,
            output_tokens=5,
        )

        with patch("luthien_proxy.policies.tool_call_judge_utils.completion", return_value=mock_result):
            result = await call_judge(
                name="test_tool",
                arguments='{"key": "value"}',
                config=config,
                judge_instructions="You are a judge",
            )

        assert result.probability == 0.75
        assert result.explanation == "somewhat dangerous"
        assert len(result.prompt) == 2

    @pytest.mark.asyncio
    async def test_call_judge_clamps_probability(self):
        """Test that probabilities outside [0,1] are clamped."""
        config = JudgeConfig(
            model="test-model",
            base_url=None,
            api_key=None,
            probability_threshold=0.5,
            temperature=0.0,
            max_tokens=256,
        )

        mock_result = CompletionResult(
            text='{"probability": 1.5, "explanation": "test"}',
            input_tokens=10,
            output_tokens=5,
        )

        with patch("luthien_proxy.policies.tool_call_judge_utils.completion", return_value=mock_result):
            result = await call_judge(
                name="test_tool",
                arguments="{}",
                config=config,
                judge_instructions="You are a judge",
            )

        # Should be clamped to 1.0
        assert result.probability == 1.0

    @pytest.mark.asyncio
    async def test_call_judge_fails_on_missing_probability(self):
        """Test that missing probability field raises ValueError (fail-secure).

        This is a security-critical test: if the judge response is malformed
        and lacks a probability field, we must NOT default to 0.0 (allow).
        Instead, we raise an exception to block the request.
        """
        config = JudgeConfig(
            model="test-model",
            base_url=None,
            api_key=None,
            probability_threshold=0.5,
            temperature=0.0,
            max_tokens=256,
        )

        mock_result = CompletionResult(
            text='{"explanation": "test without probability"}',
            input_tokens=10,
            output_tokens=5,
        )

        with patch("luthien_proxy.policies.tool_call_judge_utils.completion", return_value=mock_result):
            with pytest.raises(ValueError, match="missing required 'probability' field"):
                await call_judge(
                    name="test_tool",
                    arguments="{}",
                    config=config,
                    judge_instructions="You are a judge",
                )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
