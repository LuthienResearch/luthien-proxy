"""Unit tests for tool_call_judge_utils module.

Tests for the utility functions used by ToolCallJudgePolicy:
- Judge prompt building
- Judge response parsing (JSON, fenced code blocks, error handling)
- Blocked response creation with templates
- Judge LLM calling with configuration
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from litellm.types.utils import ModelResponse

from luthien_proxy.policies.tool_call_judge_utils import (
    JudgeConfig,
    JudgeResult,
    build_judge_prompt,
    call_judge,
    create_blocked_response,
    parse_judge_response,
)


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


class TestCreateBlockedResponse:
    """Test blocked response creation."""

    def test_create_blocked_response(self):
        """Test creating blocked response with template."""
        tool_call = {
            "name": "dangerous_tool",
            "arguments": '{"action": "delete"}',
        }

        judge_result = JudgeResult(
            probability=0.9,
            explanation="very dangerous",
            prompt=[],
            response_text="",
        )

        template = "BLOCKED: {tool_name} - {explanation} (prob: {probability:.2f})"

        response = create_blocked_response(tool_call, judge_result, template, model="test-model")

        assert response.choices[0].message.content is not None
        content = response.choices[0].message.content
        assert "dangerous_tool" in content
        assert "very dangerous" in content
        assert "0.90" in content

    def test_create_blocked_response_non_string_arguments(self):
        """Test creating blocked response when arguments is a dict."""
        tool_call = {
            "name": "test_tool",
            "arguments": {"key": "value"},  # Dict, not string
        }

        judge_result = JudgeResult(
            probability=0.8,
            explanation="test",
            prompt=[],
            response_text="",
        )

        template = "BLOCKED: {tool_name} - {tool_arguments}"

        response = create_blocked_response(tool_call, judge_result, template, model="test-model")

        content = response.choices[0].message.content
        assert "test_tool" in content
        # Arguments should be JSON-stringified
        assert '"key"' in content or "key" in content


class TestCallJudge:
    """Test the judge calling functionality."""

    @pytest.mark.asyncio
    async def test_call_judge_success(self):
        """Test successful judge call and result parsing."""
        config = JudgeConfig(
            model="test-model",
            api_base=None,
            api_key=None,
            probability_threshold=0.5,
            temperature=0.0,
            max_tokens=256,
        )

        # Mock acompletion
        mock_response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": '{"probability": 0.75, "explanation": "somewhat dangerous"}',
                    },
                    "finish_reason": "stop",
                }
            ],
        )

        with patch("luthien_proxy.policies.tool_call_judge_utils.acompletion", return_value=mock_response):
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
            api_base=None,
            api_key=None,
            probability_threshold=0.5,
            temperature=0.0,
            max_tokens=256,
        )

        # Mock response with out-of-range probability
        mock_response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": '{"probability": 1.5, "explanation": "test"}',
                    },
                    "finish_reason": "stop",
                }
            ],
        )

        with patch("luthien_proxy.policies.tool_call_judge_utils.acompletion", return_value=mock_response):
            result = await call_judge(
                name="test_tool",
                arguments="{}",
                config=config,
                judge_instructions="You are a judge",
            )

        # Should be clamped to 1.0
        assert result.probability == 1.0

    @pytest.mark.asyncio
    async def test_call_judge_uses_response_format_for_supported_models(self):
        """Test that response_format is used for supported models."""
        config = JudgeConfig(
            model="gpt-4o",  # Supported model
            api_base=None,
            api_key=None,
            probability_threshold=0.5,
            temperature=0.0,
            max_tokens=256,
        )

        mock_response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": '{"probability": 0.5, "explanation": "test"}',
                    },
                    "finish_reason": "stop",
                }
            ],
        )

        with patch(
            "luthien_proxy.policies.tool_call_judge_utils.acompletion", return_value=mock_response
        ) as mock_acompletion:
            await call_judge(
                name="test_tool",
                arguments="{}",
                config=config,
                judge_instructions="You are a judge",
            )

        # Verify response_format was passed
        call_kwargs = mock_acompletion.call_args[1]
        assert "response_format" in call_kwargs
        assert call_kwargs["response_format"]["type"] == "json_object"

    @pytest.mark.asyncio
    async def test_call_judge_skips_response_format_for_base_gpt4(self):
        """Test that response_format is NOT used for base gpt-4."""
        config = JudgeConfig(
            model="gpt-4",  # Base model doesn't support response_format
            api_base=None,
            api_key=None,
            probability_threshold=0.5,
            temperature=0.0,
            max_tokens=256,
        )

        mock_response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": '{"probability": 0.5, "explanation": "test"}',
                    },
                    "finish_reason": "stop",
                }
            ],
        )

        with patch(
            "luthien_proxy.policies.tool_call_judge_utils.acompletion", return_value=mock_response
        ) as mock_acompletion:
            await call_judge(
                name="test_tool",
                arguments="{}",
                config=config,
                judge_instructions="You are a judge",
            )

        # Verify response_format was NOT passed
        call_kwargs = mock_acompletion.call_args[1]
        assert "response_format" not in call_kwargs

    @pytest.mark.asyncio
    async def test_call_judge_fails_on_missing_probability(self):
        """Test that missing probability field raises ValueError (fail-secure).

        This is a security-critical test: if the judge response is malformed
        and lacks a probability field, we must NOT default to 0.0 (allow).
        Instead, we raise an exception to block the request.
        """
        config = JudgeConfig(
            model="test-model",
            api_base=None,
            api_key=None,
            probability_threshold=0.5,
            temperature=0.0,
            max_tokens=256,
        )

        # Response missing the required 'probability' field
        mock_response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": '{"explanation": "test without probability"}',
                    },
                    "finish_reason": "stop",
                }
            ],
        )

        with patch("luthien_proxy.policies.tool_call_judge_utils.acompletion", return_value=mock_response):
            with pytest.raises(ValueError, match="missing required 'probability' field"):
                await call_judge(
                    name="test_tool",
                    arguments="{}",
                    config=config,
                    judge_instructions="You are a judge",
                )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
