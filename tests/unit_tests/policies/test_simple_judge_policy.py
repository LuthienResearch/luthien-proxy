"""Unit tests for SimpleJudgePolicy.

Tests cover both OpenAI and Anthropic interfaces:
1. Policy initialization and configuration
2. judge_instructions property
3. OpenAI interface (via SimplePolicy hooks)
4. Anthropic interface methods
5. Blocking behavior when threshold is exceeded
"""

from __future__ import annotations

import json
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

import pytest
from anthropic.types import (
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawMessageDeltaEvent,
    RawMessageStartEvent,
    RawMessageStopEvent,
    TextBlock,
    TextDelta,
)
from litellm.types.utils import ChatCompletionMessageToolCall, Function

from luthien_proxy.llm.types.anthropic import (
    AnthropicRequest,
    AnthropicResponse,
    AnthropicTextBlock,
    AnthropicToolUseBlock,
)
from luthien_proxy.policies import PolicyContext
from luthien_proxy.policies.simple_judge_policy import SimpleJudgePolicy
from luthien_proxy.policies.simple_policy import SimplePolicy
from luthien_proxy.policies.tool_call_judge_utils import JudgeResult
from luthien_proxy.policy_core import AnthropicPolicyInterface, BasePolicy, OpenAIPolicyInterface


def make_judge_result(probability: float, explanation: str) -> JudgeResult:
    """Create a JudgeResult for testing with all required fields."""
    return JudgeResult(
        probability=probability,
        explanation=explanation,
        prompt=[{"role": "system", "content": "test"}],
        response_text='{"probability": ' + str(probability) + "}",
    )


# ========== Initialization and Configuration Tests ==========


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

    def test_implements_base_policy(self):
        """Test that SimpleJudgePolicy inherits from BasePolicy."""
        policy = SimpleJudgePolicy()
        assert isinstance(policy, BasePolicy)

    def test_implements_openai_interface(self):
        """Test that SimpleJudgePolicy implements OpenAIPolicyInterface."""
        policy = SimpleJudgePolicy()
        assert isinstance(policy, OpenAIPolicyInterface)

    def test_implements_anthropic_interface(self):
        """Test that SimpleJudgePolicy implements AnthropicPolicyInterface."""
        policy = SimpleJudgePolicy()
        assert isinstance(policy, AnthropicPolicyInterface)


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


# ========== OpenAI Interface Tests ==========


class TestSimpleJudgePolicyOpenAIRequest:
    """Test OpenAI request handling (via simple_on_request)."""

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


class TestSimpleJudgePolicyOpenAIResponseContent:
    """Test OpenAI response content handling (via simple_on_response_content)."""

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


class TestSimpleJudgePolicyOpenAIToolCall:
    """Test OpenAI tool call handling (via simple_on_response_tool_call)."""

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
        modified_args = json.loads(result.function.arguments)
        assert "error" in modified_args
        assert "Tool call blocked" in modified_args["error"]
        assert "reason" in modified_args
        assert "confidence" in modified_args

        context.record_event.assert_called_once()
        event_data = context.record_event.call_args[0][1]
        assert event_data["blocked"] is True


# ========== Anthropic Interface Tests ==========


class TestSimpleJudgePolicyAnthropicRequest:
    """Tests for Anthropic request handling."""

    @pytest.fixture
    def policy_with_rules(self) -> SimpleJudgePolicy:
        """Create a policy subclass with rules for testing."""

        class TestPolicy(SimpleJudgePolicy):
            RULES = ["Never allow rm -rf commands", "Block requests to delete data"]

        return TestPolicy(block_threshold=0.7)

    @pytest.mark.asyncio
    async def test_on_anthropic_request_passthrough_without_rules(self):
        """on_anthropic_request passes through unchanged when no rules defined."""
        policy = SimpleJudgePolicy()
        ctx = PolicyContext.for_testing()

        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
        }

        result = await policy.on_anthropic_request(request, ctx)

        assert result is request

    @pytest.mark.asyncio
    async def test_on_anthropic_request_allows_safe_content(self, policy_with_rules):
        """on_anthropic_request allows content that doesn't violate rules."""
        ctx = PolicyContext.for_testing()

        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "What is the weather today?"}],
            "max_tokens": 100,
        }

        safe_result = JudgeResult(
            probability=0.1,
            explanation="Safe request about weather",
            prompt=[],
            response_text="{}",
        )

        with patch(
            "luthien_proxy.policies.simple_judge_policy.call_judge",
            new_callable=AsyncMock,
            return_value=safe_result,
        ):
            result = await policy_with_rules.on_anthropic_request(request, ctx)

        assert result is request

    @pytest.mark.asyncio
    async def test_on_anthropic_request_blocks_unsafe_content(self, policy_with_rules):
        """on_anthropic_request raises ValueError when content violates rules."""
        ctx = PolicyContext.for_testing()

        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Run rm -rf /"}],
            "max_tokens": 100,
        }

        unsafe_result = JudgeResult(
            probability=0.9,
            explanation="Contains dangerous rm -rf command",
            prompt=[],
            response_text="{}",
        )

        with patch(
            "luthien_proxy.policies.simple_judge_policy.call_judge",
            new_callable=AsyncMock,
            return_value=unsafe_result,
        ):
            with pytest.raises(ValueError) as exc_info:
                await policy_with_rules.on_anthropic_request(request, ctx)

        assert "blocked" in str(exc_info.value).lower()
        assert "rm -rf" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_on_anthropic_request_extracts_system_content(self, policy_with_rules):
        """on_anthropic_request includes system prompt in evaluation."""
        ctx = PolicyContext.for_testing()

        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
            "system": "You are a helpful assistant",
        }

        safe_result = JudgeResult(
            probability=0.1,
            explanation="Safe request",
            prompt=[],
            response_text="{}",
        )

        with patch(
            "luthien_proxy.policies.simple_judge_policy.call_judge",
            new_callable=AsyncMock,
            return_value=safe_result,
        ) as mock_judge:
            await policy_with_rules.on_anthropic_request(request, ctx)

        # Verify call_judge was called with content including system
        call_args = mock_judge.call_args
        arguments = json.loads(call_args.kwargs["arguments"])
        assert "system:" in arguments["content"]
        assert "helpful assistant" in arguments["content"]


class TestSimpleJudgePolicyAnthropicResponse:
    """Tests for Anthropic response handling."""

    @pytest.fixture
    def policy_with_rules(self) -> SimpleJudgePolicy:
        """Create a policy subclass with rules for testing."""

        class TestPolicy(SimpleJudgePolicy):
            RULES = ["Never allow rm -rf commands", "Block requests to delete data"]

        return TestPolicy(block_threshold=0.7)

    @pytest.mark.asyncio
    async def test_on_anthropic_response_passthrough_without_rules(self):
        """on_anthropic_response passes through unchanged when no rules defined."""
        policy = SimpleJudgePolicy()
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "Hello!"}
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_anthropic_response(response, ctx)

        assert result is response

    @pytest.mark.asyncio
    async def test_on_anthropic_response_allows_safe_content(self, policy_with_rules):
        """on_anthropic_response allows content that doesn't violate rules."""
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "The weather is sunny!"}
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        safe_result = JudgeResult(
            probability=0.1,
            explanation="Safe weather response",
            prompt=[],
            response_text="{}",
        )

        with patch(
            "luthien_proxy.policies.simple_judge_policy.call_judge",
            new_callable=AsyncMock,
            return_value=safe_result,
        ):
            result = await policy_with_rules.on_anthropic_response(response, ctx)

        result_text = cast(AnthropicTextBlock, result["content"][0])
        assert result_text["text"] == "The weather is sunny!"

    @pytest.mark.asyncio
    async def test_on_anthropic_response_blocks_unsafe_content(self, policy_with_rules):
        """on_anthropic_response replaces text with blocked message when content violates rules."""
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "Here's how to run rm -rf /"}
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        unsafe_result = JudgeResult(
            probability=0.9,
            explanation="Contains dangerous command",
            prompt=[],
            response_text="{}",
        )

        with patch(
            "luthien_proxy.policies.simple_judge_policy.call_judge",
            new_callable=AsyncMock,
            return_value=unsafe_result,
        ):
            result = await policy_with_rules.on_anthropic_response(response, ctx)

        result_text = cast(AnthropicTextBlock, result["content"][0])
        assert "[Content blocked" in result_text["text"]
        assert "dangerous command" in result_text["text"]


class TestSimpleJudgePolicyAnthropicToolUse:
    """Tests for Anthropic tool use evaluation."""

    @pytest.fixture
    def policy_with_rules(self) -> SimpleJudgePolicy:
        """Create a policy subclass with rules for testing."""

        class TestPolicy(SimpleJudgePolicy):
            RULES = ["Never allow file deletion", "Block destructive operations"]

        return TestPolicy(block_threshold=0.7)

    @pytest.mark.asyncio
    async def test_on_anthropic_response_evaluates_tool_use(self, policy_with_rules):
        """on_anthropic_response evaluates tool_use blocks with judge."""
        ctx = PolicyContext.for_testing()

        tool_block: AnthropicToolUseBlock = {
            "type": "tool_use",
            "id": "tool_123",
            "name": "read_file",
            "input": {"path": "/etc/passwd"},
        }
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [tool_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        safe_result = JudgeResult(
            probability=0.2,
            explanation="Reading file is safe",
            prompt=[],
            response_text="{}",
        )

        with patch(
            "luthien_proxy.policies.simple_judge_policy.call_judge",
            new_callable=AsyncMock,
            return_value=safe_result,
        ):
            result = await policy_with_rules.on_anthropic_response(response, ctx)

        result_tool = cast(AnthropicToolUseBlock, result["content"][0])
        assert result_tool["input"] == {"path": "/etc/passwd"}

    @pytest.mark.asyncio
    async def test_on_anthropic_response_blocks_unsafe_tool_use(self, policy_with_rules):
        """on_anthropic_response replaces tool input when tool use violates rules."""
        ctx = PolicyContext.for_testing()

        tool_block: AnthropicToolUseBlock = {
            "type": "tool_use",
            "id": "tool_123",
            "name": "delete_file",
            "input": {"path": "/important/data"},
        }
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [tool_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        unsafe_result = JudgeResult(
            probability=0.9,
            explanation="File deletion is blocked",
            prompt=[],
            response_text="{}",
        )

        with patch(
            "luthien_proxy.policies.simple_judge_policy.call_judge",
            new_callable=AsyncMock,
            return_value=unsafe_result,
        ):
            result = await policy_with_rules.on_anthropic_response(response, ctx)

        result_tool = cast(AnthropicToolUseBlock, result["content"][0])
        assert "error" in result_tool["input"]
        assert "blocked" in str(result_tool["input"]["error"]).lower()

    @pytest.mark.asyncio
    async def test_on_anthropic_response_evaluates_mixed_content(self, policy_with_rules):
        """on_anthropic_response evaluates both text and tool_use blocks."""
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "Let me read that file"}
        tool_block: AnthropicToolUseBlock = {
            "type": "tool_use",
            "id": "tool_123",
            "name": "read_file",
            "input": {"path": "/data/info.txt"},
        }
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block, tool_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 10},
        }

        safe_result = JudgeResult(
            probability=0.1,
            explanation="Safe operation",
            prompt=[],
            response_text="{}",
        )

        with patch(
            "luthien_proxy.policies.simple_judge_policy.call_judge",
            new_callable=AsyncMock,
            return_value=safe_result,
        ) as mock_judge:
            await policy_with_rules.on_anthropic_response(response, ctx)

        # Should be called twice: once for text content, once for tool use
        assert mock_judge.call_count == 2


class TestSimpleJudgePolicyAnthropicStreamEvent:
    """Tests for Anthropic stream event behavior.

    SimpleJudgePolicy inherits from SimplePolicy, which buffers content blocks
    for transformation. This means:
    - Content block deltas are buffered (returns None)
    - Content block start/stop trigger buffering logic
    - Message-level events pass through
    """

    @pytest.fixture
    def policy_with_rules(self) -> SimpleJudgePolicy:
        """Create a policy subclass with rules for testing."""

        class TestPolicy(SimpleJudgePolicy):
            RULES = ["Never allow dangerous content"]

        return TestPolicy()

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_buffers_text_delta(self, policy_with_rules):
        """on_anthropic_stream_event buffers text_delta events (SimplePolicy behavior)."""
        ctx = PolicyContext.for_testing()

        # Need to start a content block first
        start_event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=TextBlock.model_construct(type="text", text=""),
        )
        await policy_with_rules.on_anthropic_stream_event(start_event, ctx)

        # Text deltas are buffered (return None)
        text_delta = TextDelta.model_construct(type="text_delta", text="Hello world")
        event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=text_delta,
        )

        result = await policy_with_rules.on_anthropic_stream_event(event, ctx)

        # SimplePolicy buffers content deltas, so it returns None
        assert result is None

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_passes_through_message_start(self, policy_with_rules):
        """on_anthropic_stream_event passes through message_start events unchanged."""
        ctx = PolicyContext.for_testing()

        event = RawMessageStartEvent.model_construct(
            type="message_start",
            message={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": "claude-sonnet-4-20250514",
                "stop_reason": None,
                "usage": {"input_tokens": 5, "output_tokens": 0},
            },
        )

        result = await policy_with_rules.on_anthropic_stream_event(event, ctx)

        assert result is event

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_passes_through_content_block_start(self, policy_with_rules):
        """on_anthropic_stream_event passes through content_block_start events."""
        ctx = PolicyContext.for_testing()

        event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=TextBlock.model_construct(type="text", text=""),
        )

        result = await policy_with_rules.on_anthropic_stream_event(event, ctx)

        assert result is event

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_passes_through_message_delta(self, policy_with_rules):
        """on_anthropic_stream_event passes through message_delta events unchanged."""
        ctx = PolicyContext.for_testing()

        event = RawMessageDeltaEvent.model_construct(
            type="message_delta",
            delta={"stop_reason": "end_turn", "stop_sequence": None},
            usage={"output_tokens": 10},
        )

        result = await policy_with_rules.on_anthropic_stream_event(event, ctx)

        assert result is event

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_passes_through_message_stop(self, policy_with_rules):
        """on_anthropic_stream_event passes through message_stop events unchanged."""
        ctx = PolicyContext.for_testing()

        event = RawMessageStopEvent.model_construct(type="message_stop")

        result = await policy_with_rules.on_anthropic_stream_event(event, ctx)

        assert result is event


# ========== Content Extraction Tests ==========


class TestSimpleJudgePolicyContentExtraction:
    """Tests for content extraction helper methods."""

    def test_extract_request_content_simple_string(self):
        """_extract_request_content handles simple string content."""
        policy = SimpleJudgePolicy()

        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello world"}],
            "max_tokens": 100,
        }

        content = policy._extract_request_content(request)

        assert "user: Hello world" in content

    def test_extract_request_content_with_blocks(self):
        """_extract_request_content handles content block arrays."""
        policy = SimpleJudgePolicy()

        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "Block content"}],
                }
            ],
            "max_tokens": 100,
        }

        content = policy._extract_request_content(request)

        assert "user: Block content" in content

    def test_extract_request_content_with_system_string(self):
        """_extract_request_content includes system string."""
        policy = SimpleJudgePolicy()

        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
            "system": "You are helpful",
        }

        content = policy._extract_request_content(request)

        assert "system: You are helpful" in content

    def test_extract_request_content_with_system_blocks(self):
        """_extract_request_content handles system block arrays."""
        policy = SimpleJudgePolicy()

        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
            "system": [{"type": "text", "text": "System block content"}],
        }

        content = policy._extract_request_content(request)

        assert "system: System block content" in content

    def test_extract_response_content_text_blocks(self):
        """_extract_response_content extracts text from blocks."""
        policy = SimpleJudgePolicy()

        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "text", "text": "First part"},
                {"type": "text", "text": "Second part"},
            ],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 10},
        }

        content = policy._extract_response_content(response)

        assert "First part" in content
        assert "Second part" in content

    def test_extract_response_content_skips_tool_use(self):
        """_extract_response_content skips tool_use blocks."""
        policy = SimpleJudgePolicy()

        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Some text"},
                {"type": "tool_use", "id": "t1", "name": "tool", "input": {}},
            ],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 10},
        }

        content = policy._extract_response_content(response)

        assert "Some text" in content
        assert "tool_use" not in content


# ========== Threshold Tests ==========


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


# ========== Event Recording Tests ==========


class TestSimpleJudgePolicyEventRecording:
    """Tests for policy event recording."""

    @pytest.fixture
    def policy_with_rules(self) -> SimpleJudgePolicy:
        """Create a policy subclass with rules for testing."""

        class TestPolicy(SimpleJudgePolicy):
            RULES = ["Test rule"]

        return TestPolicy()

    @pytest.mark.asyncio
    async def test_on_anthropic_request_records_evaluation_event(self, policy_with_rules):
        """on_anthropic_request records evaluation event to context."""
        ctx = Mock(spec=PolicyContext)
        ctx.record_event = Mock()

        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Test content"}],
            "max_tokens": 100,
        }

        safe_result = JudgeResult(
            probability=0.3,
            explanation="Safe request",
            prompt=[],
            response_text="{}",
        )

        with patch(
            "luthien_proxy.policies.simple_judge_policy.call_judge",
            new_callable=AsyncMock,
            return_value=safe_result,
        ):
            await policy_with_rules.on_anthropic_request(request, ctx)

        ctx.record_event.assert_called_once()
        call_args = ctx.record_event.call_args
        event_type = call_args[0][0]
        event_data = call_args[0][1]
        assert event_type == "policy.simple_judge.anthropic_request_evaluated"
        assert event_data["probability"] == 0.3
        assert event_data["explanation"] == "Safe request"
        assert event_data["blocked"] is False

    @pytest.mark.asyncio
    async def test_on_anthropic_response_records_evaluation_event(self, policy_with_rules):
        """on_anthropic_response records evaluation event to context."""
        ctx = Mock(spec=PolicyContext)
        ctx.record_event = Mock()

        text_block: AnthropicTextBlock = {"type": "text", "text": "Response text"}
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        safe_result = JudgeResult(
            probability=0.2,
            explanation="Safe response",
            prompt=[],
            response_text="{}",
        )

        with patch(
            "luthien_proxy.policies.simple_judge_policy.call_judge",
            new_callable=AsyncMock,
            return_value=safe_result,
        ):
            await policy_with_rules.on_anthropic_response(response, ctx)

        ctx.record_event.assert_called_once()
        call_args = ctx.record_event.call_args
        event_type = call_args[0][0]
        event_data = call_args[0][1]
        assert event_type == "policy.simple_judge.anthropic_response_evaluated"
        assert event_data["probability"] == 0.2
        assert event_data["blocked"] is False


# ========== Error Handling Tests ==========


class TestSimpleJudgePolicyErrorHandling:
    """Tests that SimpleJudgePolicy raises errors for malformed tool_use blocks."""

    @pytest.fixture
    def policy_with_rules(self) -> SimpleJudgePolicy:
        """Create a policy subclass with rules for testing."""

        class TestPolicy(SimpleJudgePolicy):
            RULES = ["Test rule"]

        return TestPolicy()

    @pytest.mark.asyncio
    async def test_on_anthropic_response_raises_on_missing_tool_use_id(self, policy_with_rules):
        """on_anthropic_response raises ValueError when tool_use block is missing id."""
        ctx = PolicyContext.for_testing()

        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "name": "test_tool",
                    "input": {},
                }  # Missing "id" field
            ],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        with pytest.raises(ValueError) as exc_info:
            await policy_with_rules.on_anthropic_response(response, ctx)

        assert "Malformed tool_use block" in str(exc_info.value)
        assert "id=None" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_on_anthropic_response_raises_on_missing_tool_use_name(self, policy_with_rules):
        """on_anthropic_response raises ValueError when tool_use block is missing name."""
        ctx = PolicyContext.for_testing()

        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tool_123",
                    "input": {},
                }  # Missing "name" field
            ],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        with pytest.raises(ValueError) as exc_info:
            await policy_with_rules.on_anthropic_response(response, ctx)

        assert "Malformed tool_use block" in str(exc_info.value)
        assert "name=None" in str(exc_info.value)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
