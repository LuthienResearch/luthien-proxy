# ABOUTME: Tests for AnthropicToolCallJudgePolicy verifying tool call evaluation and blocking
"""Tests for AnthropicToolCallJudgePolicy.

Verifies that AnthropicToolCallJudgePolicy:
1. Implements the AnthropicPolicyProtocol
2. Passes through requests unchanged
3. Evaluates and potentially blocks tool_use in non-streaming responses
4. Buffers and evaluates tool_use deltas in streaming
5. Handles judge failures with fail-secure behavior
"""

from typing import cast
from unittest.mock import patch

import pytest
from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    RawMessageStartEvent,
    RawMessageStopEvent,
    TextBlock,
    TextDelta,
    ToolUseBlock,
)

from luthien_proxy.llm.types.anthropic import (
    AnthropicRequest,
    AnthropicResponse,
    AnthropicTextBlock,
    AnthropicToolUseBlock,
)
from luthien_proxy.policies.anthropic.tool_call_judge import AnthropicToolCallJudgePolicy
from luthien_proxy.policies.tool_call_judge_utils import JudgeResult
from luthien_proxy.policy_core.anthropic_protocol import AnthropicPolicyProtocol
from luthien_proxy.policy_core.policy_context import PolicyContext


def make_judge_result(probability: float, explanation: str = "test") -> JudgeResult:
    """Create a JudgeResult for testing."""
    return JudgeResult(
        probability=probability,
        explanation=explanation,
        prompt=[],
        response_text="",
    )


class TestAnthropicToolCallJudgePolicyProtocol:
    """Tests verifying AnthropicToolCallJudgePolicy implements the protocol."""

    def test_implements_protocol(self):
        """AnthropicToolCallJudgePolicy satisfies AnthropicPolicyProtocol."""
        policy = AnthropicToolCallJudgePolicy()
        assert isinstance(policy, AnthropicPolicyProtocol)

    def test_has_short_policy_name(self):
        """AnthropicToolCallJudgePolicy has correct short_policy_name property."""
        policy = AnthropicToolCallJudgePolicy()
        assert policy.short_policy_name == "AnthropicToolJudge"


class TestAnthropicToolCallJudgePolicyConfiguration:
    """Tests for policy configuration and initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default configuration."""
        policy = AnthropicToolCallJudgePolicy()

        assert policy._config.model == "openai/gpt-4"
        assert policy._config.probability_threshold == 0.6
        assert policy._config.temperature == 0.0
        assert policy._config.max_tokens == 256

    def test_init_with_custom_config(self):
        """Test initialization with custom configuration."""
        policy = AnthropicToolCallJudgePolicy(
            model="claude-3-5-sonnet-20241022",
            api_base="http://custom:8000",
            api_key="test-key",
            probability_threshold=0.8,
            temperature=0.5,
            max_tokens=512,
            judge_instructions="Custom instructions",
            blocked_message_template="Custom template: {tool_name}",
        )

        assert policy._config.model == "claude-3-5-sonnet-20241022"
        assert policy._config.api_base == "http://custom:8000"
        assert policy._config.api_key == "test-key"
        assert policy._config.probability_threshold == 0.8
        assert policy._config.temperature == 0.5
        assert policy._config.max_tokens == 512
        assert policy._judge_instructions == "Custom instructions"
        assert "Custom template" in policy._blocked_message_template

    def test_init_invalid_threshold_raises(self):
        """Test that invalid probability threshold raises ValueError."""
        with pytest.raises(ValueError, match="probability_threshold must be between 0 and 1"):
            AnthropicToolCallJudgePolicy(probability_threshold=1.5)

        with pytest.raises(ValueError, match="probability_threshold must be between 0 and 1"):
            AnthropicToolCallJudgePolicy(probability_threshold=-0.1)


class TestAnthropicToolCallJudgePolicyRequest:
    """Tests for on_request passthrough behavior."""

    @pytest.mark.asyncio
    async def test_on_request_returns_same_request(self):
        """on_request returns the exact same request object unchanged."""
        policy = AnthropicToolCallJudgePolicy()
        ctx = PolicyContext.for_testing()

        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
        }

        result = await policy.on_request(request, ctx)

        assert result is request

    @pytest.mark.asyncio
    async def test_on_request_preserves_all_fields(self):
        """on_request preserves all fields including tools."""
        policy = AnthropicToolCallJudgePolicy()
        ctx = PolicyContext.for_testing()

        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "What's the weather?"}],
            "max_tokens": 500,
            "tools": [
                {
                    "name": "get_weather",
                    "description": "Get weather for a location",
                    "input_schema": {
                        "type": "object",
                        "properties": {"location": {"type": "string"}},
                    },
                }
            ],
        }

        result = await policy.on_request(request, ctx)

        assert result["model"] == "claude-sonnet-4-20250514"
        assert len(result.get("tools", [])) == 1


class TestAnthropicToolCallJudgePolicyResponseNoToolUse:
    """Tests for on_response when there are no tool_use blocks."""

    @pytest.mark.asyncio
    async def test_on_response_passthrough_text_only(self):
        """on_response passes through responses with only text blocks."""
        policy = AnthropicToolCallJudgePolicy()
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "Hello, world!"}
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_response(response, ctx)

        # Should pass through unchanged (same object reference possible)
        assert result["content"] == response["content"]
        assert result.get("stop_reason") == "end_turn"

    @pytest.mark.asyncio
    async def test_on_response_empty_content(self):
        """on_response handles empty content list."""
        policy = AnthropicToolCallJudgePolicy()
        ctx = PolicyContext.for_testing()

        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 0},
        }

        result = await policy.on_response(response, ctx)

        assert result is response


class TestAnthropicToolCallJudgePolicyResponseToolUse:
    """Tests for on_response with tool_use blocks."""

    @pytest.mark.asyncio
    async def test_on_response_allows_safe_tool_call(self):
        """on_response allows tool_use blocks judged as safe."""
        policy = AnthropicToolCallJudgePolicy(probability_threshold=0.5)
        ctx = PolicyContext.for_testing()

        tool_use_block: AnthropicToolUseBlock = {
            "type": "tool_use",
            "id": "tool_123",
            "name": "get_weather",
            "input": {"location": "San Francisco"},
        }
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [tool_use_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        async def mock_call_judge(*args, **kwargs):
            return make_judge_result(probability=0.2, explanation="safe operation")

        with patch(
            "luthien_proxy.policies.anthropic.tool_call_judge.call_judge",
            side_effect=mock_call_judge,
        ):
            result = await policy.on_response(response, ctx)

        # Tool use should be preserved
        result_tool_block = cast(AnthropicToolUseBlock, result["content"][0])
        assert result_tool_block["type"] == "tool_use"
        assert result_tool_block["name"] == "get_weather"
        assert result.get("stop_reason") == "tool_use"

    @pytest.mark.asyncio
    async def test_on_response_blocks_harmful_tool_call(self):
        """on_response blocks tool_use blocks judged as harmful."""
        policy = AnthropicToolCallJudgePolicy(probability_threshold=0.5)
        ctx = PolicyContext.for_testing()

        tool_use_block: AnthropicToolUseBlock = {
            "type": "tool_use",
            "id": "tool_123",
            "name": "rm_rf",
            "input": {"path": "/"},
        }
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [tool_use_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        async def mock_call_judge(*args, **kwargs):
            return make_judge_result(probability=0.9, explanation="dangerous operation")

        with patch(
            "luthien_proxy.policies.anthropic.tool_call_judge.call_judge",
            side_effect=mock_call_judge,
        ):
            result = await policy.on_response(response, ctx)

        # Tool use should be replaced with text
        result_block = cast(AnthropicTextBlock, result["content"][0])
        assert result_block["type"] == "text"
        assert "rm_rf" in result_block["text"]
        assert "rejected" in result_block["text"].lower()
        # stop_reason should change from tool_use to end_turn
        assert result.get("stop_reason") == "end_turn"

    @pytest.mark.asyncio
    async def test_on_response_mixed_content_partial_block(self):
        """on_response handles mixed content where only some tool_use is blocked."""
        policy = AnthropicToolCallJudgePolicy(probability_threshold=0.5)
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "Let me help you"}
        tool_use_block: AnthropicToolUseBlock = {
            "type": "tool_use",
            "id": "tool_123",
            "name": "dangerous_tool",
            "input": {"arg": "value"},
        }
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block, tool_use_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 15},
        }

        async def mock_call_judge(*args, **kwargs):
            return make_judge_result(probability=0.8, explanation="blocked")

        with patch(
            "luthien_proxy.policies.anthropic.tool_call_judge.call_judge",
            side_effect=mock_call_judge,
        ):
            result = await policy.on_response(response, ctx)

        # Should have two text blocks now
        assert len(result["content"]) == 2
        result_block0 = cast(AnthropicTextBlock, result["content"][0])
        result_block1 = cast(AnthropicTextBlock, result["content"][1])
        assert result_block0["type"] == "text"
        assert result_block0["text"] == "Let me help you"
        assert result_block1["type"] == "text"
        assert "dangerous_tool" in result_block1["text"]


class TestAnthropicToolCallJudgePolicyErrorHandling:
    """Tests for error handling and fail-secure behavior."""

    @pytest.mark.asyncio
    async def test_judge_failure_blocks_tool_call(self):
        """on_response blocks when judge fails (fail-secure)."""
        policy = AnthropicToolCallJudgePolicy()
        ctx = PolicyContext.for_testing()

        tool_use_block: AnthropicToolUseBlock = {
            "type": "tool_use",
            "id": "tool_123",
            "name": "test_tool",
            "input": {},
        }
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [tool_use_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        async def mock_call_judge(*args, **kwargs):
            raise Exception("Judge service unavailable")

        with patch(
            "luthien_proxy.policies.anthropic.tool_call_judge.call_judge",
            side_effect=mock_call_judge,
        ):
            result = await policy.on_response(response, ctx)

        # Should be blocked due to fail-secure
        result_block = cast(AnthropicTextBlock, result["content"][0])
        assert result_block["type"] == "text"
        assert "test_tool" in result_block["text"]


class TestAnthropicToolCallJudgePolicyStreamEventNonToolUse:
    """Tests for on_stream_event with non-tool_use events."""

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_message_start(self):
        """on_stream_event passes through message_start events unchanged."""
        policy = AnthropicToolCallJudgePolicy()
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

        result = await policy.on_stream_event(event, ctx)

        assert result is event

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_text_delta(self):
        """on_stream_event passes through text_delta events unchanged."""
        policy = AnthropicToolCallJudgePolicy()
        ctx = PolicyContext.for_testing()

        text_delta = TextDelta.model_construct(type="text_delta", text="hello")
        event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=text_delta,
        )

        result = await policy.on_stream_event(event, ctx)

        assert result is event

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_text_block_start(self):
        """on_stream_event passes through text content_block_start unchanged."""
        policy = AnthropicToolCallJudgePolicy()
        ctx = PolicyContext.for_testing()

        event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=TextBlock.model_construct(type="text", text=""),
        )

        result = await policy.on_stream_event(event, ctx)

        assert result is event

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_message_stop(self):
        """on_stream_event passes through message_stop events unchanged."""
        policy = AnthropicToolCallJudgePolicy()
        ctx = PolicyContext.for_testing()

        event = RawMessageStopEvent.model_construct(type="message_stop")

        result = await policy.on_stream_event(event, ctx)

        assert result is event

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_message_delta(self):
        """on_stream_event passes through message_delta events unchanged."""
        policy = AnthropicToolCallJudgePolicy()
        ctx = PolicyContext.for_testing()

        event = RawMessageDeltaEvent.model_construct(
            type="message_delta",
            delta={"stop_reason": "end_turn", "stop_sequence": None},
            usage={"output_tokens": 10},
        )

        result = await policy.on_stream_event(event, ctx)

        assert result is event


class TestAnthropicToolCallJudgePolicyStreamEventToolUse:
    """Tests for on_stream_event with tool_use events."""

    @pytest.mark.asyncio
    async def test_on_stream_event_buffers_tool_use_start(self):
        """on_stream_event buffers tool_use content_block_start and returns None."""
        policy = AnthropicToolCallJudgePolicy()
        ctx = PolicyContext.for_testing()

        event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=ToolUseBlock.model_construct(
                type="tool_use",
                id="tool_123",
                name="get_weather",
                input={},
            ),
        )

        result = await policy.on_stream_event(event, ctx)

        # Should filter out (return None) while buffering
        assert result is None
        # Should have buffered the data
        assert 0 in policy._buffered_tool_uses
        assert policy._buffered_tool_uses[0]["id"] == "tool_123"
        assert policy._buffered_tool_uses[0]["name"] == "get_weather"

    @pytest.mark.asyncio
    async def test_on_stream_event_buffers_input_json_delta(self):
        """on_stream_event accumulates input_json_delta for buffered tool_use."""
        policy = AnthropicToolCallJudgePolicy()
        ctx = PolicyContext.for_testing()

        # First, start a tool_use block
        start_event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=ToolUseBlock.model_construct(
                type="tool_use",
                id="tool_123",
                name="get_weather",
                input={},
            ),
        )
        await policy.on_stream_event(start_event, ctx)

        # Now send input_json_delta events
        delta1 = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=InputJSONDelta.model_construct(type="input_json_delta", partial_json='{"loc'),
        )
        delta2 = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=InputJSONDelta.model_construct(type="input_json_delta", partial_json='ation": "SF"}'),
        )

        result1 = await policy.on_stream_event(delta1, ctx)
        result2 = await policy.on_stream_event(delta2, ctx)

        # Should filter out both
        assert result1 is None
        assert result2 is None
        # Should have accumulated the JSON
        assert policy._buffered_tool_uses[0]["input_json"] == '{"location": "SF"}'

    @pytest.mark.asyncio
    async def test_on_stream_event_judges_on_block_stop_allowed(self):
        """on_stream_event judges tool call on content_block_stop and allows if safe."""
        policy = AnthropicToolCallJudgePolicy(probability_threshold=0.5)
        ctx = PolicyContext.for_testing()

        # Buffer a complete tool call
        start_event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=ToolUseBlock.model_construct(
                type="tool_use",
                id="tool_123",
                name="get_weather",
                input={},
            ),
        )
        await policy.on_stream_event(start_event, ctx)

        delta_event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=InputJSONDelta.model_construct(
                type="input_json_delta",
                partial_json='{"location": "SF"}',
            ),
        )
        await policy.on_stream_event(delta_event, ctx)

        # Now send stop event with judge allowing
        stop_event = RawContentBlockStopEvent.model_construct(
            type="content_block_stop",
            index=0,
        )

        async def mock_call_judge(*args, **kwargs):
            return make_judge_result(probability=0.2, explanation="safe")

        with patch(
            "luthien_proxy.policies.anthropic.tool_call_judge.call_judge",
            side_effect=mock_call_judge,
        ):
            result = await policy.on_stream_event(stop_event, ctx)

        # Should pass through the stop event
        assert result is stop_event
        # Buffer should be cleared
        assert 0 not in policy._buffered_tool_uses

    @pytest.mark.asyncio
    async def test_on_stream_event_judges_on_block_stop_blocked(self):
        """on_stream_event judges tool call on content_block_stop and filters if blocked."""
        policy = AnthropicToolCallJudgePolicy(probability_threshold=0.5)
        ctx = PolicyContext.for_testing()

        # Buffer a complete tool call
        start_event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=ToolUseBlock.model_construct(
                type="tool_use",
                id="tool_123",
                name="dangerous_tool",
                input={},
            ),
        )
        await policy.on_stream_event(start_event, ctx)

        delta_event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=InputJSONDelta.model_construct(
                type="input_json_delta",
                partial_json='{"arg": "value"}',
            ),
        )
        await policy.on_stream_event(delta_event, ctx)

        # Now send stop event with judge blocking
        stop_event = RawContentBlockStopEvent.model_construct(
            type="content_block_stop",
            index=0,
        )

        async def mock_call_judge(*args, **kwargs):
            return make_judge_result(probability=0.9, explanation="dangerous")

        with patch(
            "luthien_proxy.policies.anthropic.tool_call_judge.call_judge",
            side_effect=mock_call_judge,
        ):
            result = await policy.on_stream_event(stop_event, ctx)

        # Should filter out the stop event for blocked tool
        assert result is None
        # Should have marked the block as blocked
        assert 0 in policy._blocked_blocks
        # Buffer should be cleared
        assert 0 not in policy._buffered_tool_uses


class TestAnthropicToolCallJudgePolicyStreamingErrorHandling:
    """Tests for streaming error handling."""

    @pytest.mark.asyncio
    async def test_on_stream_event_judge_failure_blocks(self):
        """on_stream_event blocks on judge failure (fail-secure)."""
        policy = AnthropicToolCallJudgePolicy()
        ctx = PolicyContext.for_testing()

        # Buffer a tool call
        start_event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=ToolUseBlock.model_construct(
                type="tool_use",
                id="tool_123",
                name="test_tool",
                input={},
            ),
        )
        await policy.on_stream_event(start_event, ctx)

        delta_event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=InputJSONDelta.model_construct(type="input_json_delta", partial_json="{}"),
        )
        await policy.on_stream_event(delta_event, ctx)

        stop_event = RawContentBlockStopEvent.model_construct(
            type="content_block_stop",
            index=0,
        )

        async def mock_call_judge(*args, **kwargs):
            raise Exception("Judge failure")

        with patch(
            "luthien_proxy.policies.anthropic.tool_call_judge.call_judge",
            side_effect=mock_call_judge,
        ):
            result = await policy.on_stream_event(stop_event, ctx)

        # Should block (filter out) due to fail-secure
        assert result is None
        assert 0 in policy._blocked_blocks


class CapturingEmitter:
    """Event emitter that captures events for testing."""

    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []

    def record(self, transaction_id: str, event_type: str, data: dict) -> None:
        self.events.append((transaction_id, event_type, data))


class TestAnthropicToolCallJudgePolicyObservability:
    """Tests for observability event emission."""

    @pytest.mark.asyncio
    async def test_emits_evaluation_events(self):
        """Policy emits observability events during evaluation."""
        policy = AnthropicToolCallJudgePolicy(probability_threshold=0.5)

        # Use a capturing emitter to verify events
        emitter = CapturingEmitter()
        ctx = PolicyContext(
            transaction_id="test-txn",
            emitter=emitter,
        )

        tool_use_block: AnthropicToolUseBlock = {
            "type": "tool_use",
            "id": "tool_123",
            "name": "test_tool",
            "input": {"arg": "value"},
        }
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [tool_use_block],
            "model": "claude-sonnet-4-20250514",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        async def mock_call_judge(*args, **kwargs):
            return make_judge_result(probability=0.2, explanation="safe")

        with patch(
            "luthien_proxy.policies.anthropic.tool_call_judge.call_judge",
            side_effect=mock_call_judge,
        ):
            await policy.on_response(response, ctx)

        # Check that events were emitted
        event_types = [e[1] for e in emitter.events]
        assert "policy.anthropic_judge.evaluation_started" in event_types
        assert "policy.anthropic_judge.evaluation_complete" in event_types
        assert "policy.anthropic_judge.tool_call_allowed" in event_types


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
