"""Unit tests for SimpleJudgePolicy.

Tests cover:
1. Policy initialization and configuration
2. judge_instructions property
3. simple_on_request evaluation
4. simple_on_response_content evaluation
5. simple_on_response_tool_call evaluation
6. Blocking behavior when threshold is exceeded
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from litellm.types.utils import ChatCompletionMessageToolCall, Function

from luthien_proxy.policies import PolicyContext
from luthien_proxy.policies.simple_judge_policy import SimpleJudgePolicy
from luthien_proxy.policies.simple_policy import SimplePolicy
from luthien_proxy.policies.tool_call_judge_utils import JudgeResult


def make_judge_result(probability: float, explanation: str) -> JudgeResult:
    """Create a JudgeResult for testing with all required fields."""
    return JudgeResult(
        probability=probability,
        explanation=explanation,
        prompt=[{"role": "system", "content": "test"}],
        response_text='{"probability": ' + str(probability) + "}",
    )


class TestSimpleJudgePolicyInit:
    """Test SimpleJudgePolicy initialization."""

    def test_default_initialization(self):
        """Test default initialization values."""
        policy = SimpleJudgePolicy()

        assert policy.judge_config.model == "claude-3-5-sonnet-20241022"
        assert policy.judge_config.temperature == 0.0
        assert policy.judge_config.probability_threshold == 0.7
        assert policy.judge_config.api_base is None
        assert policy.judge_config.api_key is None

    def test_custom_initialization(self):
        """Test custom initialization values."""
        policy = SimpleJudgePolicy(
            judge_model="gpt-4",
            judge_temperature=0.5,
            judge_api_base="https://api.example.com",
            judge_api_key="test-key",
            block_threshold=0.9,
        )

        assert policy.judge_config.model == "gpt-4"
        assert policy.judge_config.temperature == 0.5
        assert policy.judge_config.api_base == "https://api.example.com"
        assert policy.judge_config.api_key == "test-key"
        assert policy.judge_config.probability_threshold == 0.9

    def test_inherits_from_simple_policy(self):
        """Test that SimpleJudgePolicy inherits from SimplePolicy."""
        policy = SimpleJudgePolicy()
        assert isinstance(policy, SimplePolicy)


class TestSimpleJudgePolicyGetConfig:
    """Test get_config method."""

    def test_get_config_returns_judge_settings(self):
        """Test that get_config returns judge configuration."""
        policy = SimpleJudgePolicy(
            judge_model="gpt-4",
            judge_temperature=0.3,
            block_threshold=0.8,
        )

        config = policy.get_config()

        assert config["judge_model"] == "gpt-4"
        assert config["judge_temperature"] == 0.3
        assert config["block_threshold"] == 0.8


class TestSimpleJudgePolicyJudgeInstructions:
    """Test judge_instructions property."""

    def test_judge_instructions_with_no_rules(self):
        """Test judge_instructions when RULES is empty."""
        policy = SimpleJudgePolicy()
        # Default RULES is empty
        instructions = policy.judge_instructions

        assert "No specific rules defined" in instructions

    def test_judge_instructions_with_rules(self):
        """Test judge_instructions when RULES is defined."""

        class CustomJudgePolicy(SimpleJudgePolicy):
            RULES = [
                "Never allow rm -rf commands",
                "Block requests to delete production data",
            ]

        policy = CustomJudgePolicy()
        instructions = policy.judge_instructions

        assert "Never allow rm -rf commands" in instructions
        assert "Block requests to delete production data" in instructions
        assert "probability" in instructions.lower()
        assert "explanation" in instructions.lower()


class TestSimpleJudgePolicySimpleOnRequest:
    """Test simple_on_request method."""

    @pytest.mark.asyncio
    async def test_simple_on_request_no_rules_passthrough(self):
        """Test that request is passed through when no rules are defined."""
        policy = SimpleJudgePolicy()
        context = Mock(spec=PolicyContext)
        context.record_event = Mock()

        result = await policy.simple_on_request("Hello world", context)

        assert result == "Hello world"
        # Should not have recorded any event (no evaluation happened)
        context.record_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_simple_on_request_safe_content(self):
        """Test that safe content passes through."""

        class CustomJudgePolicy(SimpleJudgePolicy):
            RULES = ["Block dangerous commands"]

        policy = CustomJudgePolicy()
        context = Mock(spec=PolicyContext)
        context.record_event = Mock()

        mock_result = make_judge_result(probability=0.1, explanation="Safe content")

        with patch(
            "luthien_proxy.policies.simple_judge_policy.call_judge",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await policy.simple_on_request("What is the weather?", context)

        assert result == "What is the weather?"
        context.record_event.assert_called_once()
        event_data = context.record_event.call_args[0][1]
        assert event_data["probability"] == 0.1
        assert event_data["blocked"] is False

    @pytest.mark.asyncio
    async def test_simple_on_request_blocked_content(self):
        """Test that dangerous content is blocked."""

        class CustomJudgePolicy(SimpleJudgePolicy):
            RULES = ["Block dangerous commands"]

        policy = CustomJudgePolicy()
        context = Mock(spec=PolicyContext)
        context.record_event = Mock()

        mock_result = make_judge_result(probability=0.9, explanation="Dangerous command detected")

        with patch(
            "luthien_proxy.policies.simple_judge_policy.call_judge",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            with pytest.raises(ValueError) as exc_info:
                await policy.simple_on_request("rm -rf /", context)

        assert "Request blocked" in str(exc_info.value)
        assert "Dangerous command detected" in str(exc_info.value)
        context.record_event.assert_called_once()
        event_data = context.record_event.call_args[0][1]
        assert event_data["blocked"] is True


class TestSimpleJudgePolicySimpleOnResponseContent:
    """Test simple_on_response_content method."""

    @pytest.mark.asyncio
    async def test_simple_on_response_content_no_rules_passthrough(self):
        """Test that response is passed through when no rules are defined."""
        policy = SimpleJudgePolicy()
        context = Mock(spec=PolicyContext)
        context.record_event = Mock()

        result = await policy.simple_on_response_content("Here is your answer", context)

        assert result == "Here is your answer"
        context.record_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_simple_on_response_content_safe(self):
        """Test that safe response content passes through."""

        class CustomJudgePolicy(SimpleJudgePolicy):
            RULES = ["Block harmful content"]

        policy = CustomJudgePolicy()
        context = Mock(spec=PolicyContext)
        context.record_event = Mock()

        mock_result = make_judge_result(probability=0.2, explanation="Safe response")

        with patch(
            "luthien_proxy.policies.simple_judge_policy.call_judge",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await policy.simple_on_response_content("The weather is sunny", context)

        assert result == "The weather is sunny"
        context.record_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_simple_on_response_content_blocked(self):
        """Test that harmful response content is replaced with blocked message."""

        class CustomJudgePolicy(SimpleJudgePolicy):
            RULES = ["Block harmful content"]

        policy = CustomJudgePolicy()
        context = Mock(spec=PolicyContext)
        context.record_event = Mock()

        mock_result = make_judge_result(probability=0.85, explanation="Harmful content detected")

        with patch(
            "luthien_proxy.policies.simple_judge_policy.call_judge",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await policy.simple_on_response_content("Some harmful content", context)

        assert "[Content blocked by CustomJudgePolicy" in result
        assert "Harmful content detected" in result
        context.record_event.assert_called_once()
        event_data = context.record_event.call_args[0][1]
        assert event_data["blocked"] is True


class TestSimpleJudgePolicySimpleOnResponseToolCall:
    """Test simple_on_response_tool_call method."""

    @pytest.mark.asyncio
    async def test_simple_on_response_tool_call_no_rules_passthrough(self):
        """Test that tool call is passed through when no rules are defined."""
        policy = SimpleJudgePolicy()
        context = Mock(spec=PolicyContext)
        context.record_event = Mock()

        tool_call = ChatCompletionMessageToolCall(
            id="call-123",
            type="function",
            function=Function(name="get_weather", arguments='{"location": "NYC"}'),
        )

        result = await policy.simple_on_response_tool_call(tool_call, context)

        assert result.function.name == "get_weather"
        assert result.function.arguments == '{"location": "NYC"}'
        context.record_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_simple_on_response_tool_call_safe(self):
        """Test that safe tool call passes through."""

        class CustomJudgePolicy(SimpleJudgePolicy):
            RULES = ["Block dangerous tool calls"]

        policy = CustomJudgePolicy()
        context = Mock(spec=PolicyContext)
        context.record_event = Mock()

        tool_call = ChatCompletionMessageToolCall(
            id="call-123",
            type="function",
            function=Function(name="get_weather", arguments='{"location": "NYC"}'),
        )

        mock_result = make_judge_result(probability=0.1, explanation="Safe tool call")

        with patch(
            "luthien_proxy.policies.simple_judge_policy.call_judge",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await policy.simple_on_response_tool_call(tool_call, context)

        assert result.function.name == "get_weather"
        assert result.function.arguments == '{"location": "NYC"}'
        context.record_event.assert_called_once()
        event_data = context.record_event.call_args[0][1]
        assert event_data["tool_name"] == "get_weather"
        assert event_data["blocked"] is False

    @pytest.mark.asyncio
    async def test_simple_on_response_tool_call_blocked(self):
        """Test that dangerous tool call is blocked."""

        class CustomJudgePolicy(SimpleJudgePolicy):
            RULES = ["Block dangerous tool calls"]

        policy = CustomJudgePolicy()
        context = Mock(spec=PolicyContext)
        context.record_event = Mock()

        tool_call = ChatCompletionMessageToolCall(
            id="call-123",
            type="function",
            function=Function(name="execute_command", arguments='{"command": "rm -rf /"}'),
        )

        mock_result = make_judge_result(probability=0.95, explanation="Dangerous command execution")

        with patch(
            "luthien_proxy.policies.simple_judge_policy.call_judge",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await policy.simple_on_response_tool_call(tool_call, context)

        # Tool call should be modified with error info
        import json

        modified_args = json.loads(result.function.arguments)
        assert "error" in modified_args
        assert "Tool call blocked" in modified_args["error"]
        assert "reason" in modified_args
        assert "confidence" in modified_args

        context.record_event.assert_called_once()
        event_data = context.record_event.call_args[0][1]
        assert event_data["blocked"] is True


class TestSimpleJudgePolicyThreshold:
    """Test threshold behavior."""

    @pytest.mark.asyncio
    async def test_custom_threshold_not_exceeded(self):
        """Test that custom threshold affects blocking decision."""

        class CustomJudgePolicy(SimpleJudgePolicy):
            RULES = ["Block stuff"]

        # Set high threshold
        policy = CustomJudgePolicy(block_threshold=0.95)
        context = Mock(spec=PolicyContext)
        context.record_event = Mock()

        # Probability below threshold
        mock_result = make_judge_result(probability=0.9, explanation="Somewhat suspicious")

        with patch(
            "luthien_proxy.policies.simple_judge_policy.call_judge",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await policy.simple_on_request("Test content", context)

        # Should not be blocked (0.9 < 0.95)
        assert result == "Test content"
        event_data = context.record_event.call_args[0][1]
        assert event_data["blocked"] is False

    @pytest.mark.asyncio
    async def test_custom_threshold_exceeded(self):
        """Test blocking when custom threshold is exceeded."""

        class CustomJudgePolicy(SimpleJudgePolicy):
            RULES = ["Block stuff"]

        # Set low threshold
        policy = CustomJudgePolicy(block_threshold=0.5)
        context = Mock(spec=PolicyContext)
        context.record_event = Mock()

        # Probability above threshold
        mock_result = make_judge_result(probability=0.6, explanation="Somewhat suspicious")

        with patch(
            "luthien_proxy.policies.simple_judge_policy.call_judge",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            with pytest.raises(ValueError):
                await policy.simple_on_request("Test content", context)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
