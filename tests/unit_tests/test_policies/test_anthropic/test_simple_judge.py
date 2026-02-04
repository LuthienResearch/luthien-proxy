# ABOUTME: Tests for AnthropicSimpleJudgePolicy verifying LLM-based safety evaluation
"""Tests for AnthropicSimpleJudgePolicy.

Verifies that AnthropicSimpleJudgePolicy:
1. Implements the AnthropicPolicyProtocol
2. Passes through requests/responses when no rules defined
3. Evaluates requests against rules and blocks violations
4. Evaluates response content and blocks violations
5. Evaluates tool use blocks and blocks violations
6. Passes through stream events unchanged
"""

from __future__ import annotations

import json
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from anthropic.types import (
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    RawMessageStartEvent,
    RawMessageStopEvent,
    TextBlock,
    TextDelta,
)

from luthien_proxy.llm.types.anthropic import (
    AnthropicRequest,
    AnthropicResponse,
    AnthropicTextBlock,
    AnthropicToolUseBlock,
)
from luthien_proxy.policies.anthropic.simple_judge import AnthropicSimpleJudgePolicy
from luthien_proxy.policies.tool_call_judge_utils import JudgeResult
from luthien_proxy.policy_core.anthropic_protocol import AnthropicPolicyProtocol
from luthien_proxy.policy_core.policy_context import PolicyContext


class TestAnthropicSimpleJudgePolicyProtocol:
    """Tests verifying AnthropicSimpleJudgePolicy implements the protocol."""

    def test_implements_protocol(self):
        """AnthropicSimpleJudgePolicy satisfies AnthropicPolicyProtocol."""
        policy = AnthropicSimpleJudgePolicy()
        assert isinstance(policy, AnthropicPolicyProtocol)

    def test_has_short_policy_name(self):
        """AnthropicSimpleJudgePolicy has correct short_policy_name property."""
        policy = AnthropicSimpleJudgePolicy()
        assert policy.short_policy_name == "AnthropicSimpleJudge"

    def test_get_config_returns_judge_settings(self):
        """get_config returns the judge configuration."""
        policy = AnthropicSimpleJudgePolicy(
            judge_model="test-model",
            judge_temperature=0.5,
            block_threshold=0.8,
        )
        config = policy.get_config()
        assert config["judge_model"] == "test-model"
        assert config["judge_temperature"] == 0.5
        assert config["block_threshold"] == 0.8


class TestAnthropicSimpleJudgePolicyNoRules:
    """Tests for behavior when no RULES are defined."""

    @pytest.mark.asyncio
    async def test_on_request_passthrough_without_rules(self):
        """on_request passes through unchanged when no rules defined."""
        policy = AnthropicSimpleJudgePolicy()
        ctx = PolicyContext.for_testing()

        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
        }

        result = await policy.on_request(request, ctx)

        assert result is request

    @pytest.mark.asyncio
    async def test_on_response_passthrough_without_rules(self):
        """on_response passes through unchanged when no rules defined."""
        policy = AnthropicSimpleJudgePolicy()
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

        result = await policy.on_response(response, ctx)

        assert result is response


class TestAnthropicSimpleJudgePolicyWithRules:
    """Tests for behavior with RULES defined."""

    @pytest.fixture
    def policy_with_rules(self) -> AnthropicSimpleJudgePolicy:
        """Create a policy subclass with rules for testing."""

        class TestPolicy(AnthropicSimpleJudgePolicy):
            RULES = ["Never allow rm -rf commands", "Block requests to delete data"]

        return TestPolicy(block_threshold=0.7)

    @pytest.mark.asyncio
    async def test_on_request_allows_safe_content(self, policy_with_rules):
        """on_request allows content that doesn't violate rules."""
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
            "luthien_proxy.policies.anthropic.simple_judge.call_judge",
            new_callable=AsyncMock,
            return_value=safe_result,
        ):
            result = await policy_with_rules.on_request(request, ctx)

        assert result is request

    @pytest.mark.asyncio
    async def test_on_request_blocks_unsafe_content(self, policy_with_rules):
        """on_request raises ValueError when content violates rules."""
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
            "luthien_proxy.policies.anthropic.simple_judge.call_judge",
            new_callable=AsyncMock,
            return_value=unsafe_result,
        ):
            with pytest.raises(ValueError) as exc_info:
                await policy_with_rules.on_request(request, ctx)

        assert "blocked" in str(exc_info.value).lower()
        assert "rm -rf" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_on_request_extracts_system_content(self, policy_with_rules):
        """on_request includes system prompt in evaluation."""
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
            "luthien_proxy.policies.anthropic.simple_judge.call_judge",
            new_callable=AsyncMock,
            return_value=safe_result,
        ) as mock_judge:
            await policy_with_rules.on_request(request, ctx)

        # Verify call_judge was called with content including system
        call_args = mock_judge.call_args
        arguments = json.loads(call_args.kwargs["arguments"])
        assert "system:" in arguments["content"]
        assert "helpful assistant" in arguments["content"]

    @pytest.mark.asyncio
    async def test_on_response_allows_safe_content(self, policy_with_rules):
        """on_response allows content that doesn't violate rules."""
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
            "luthien_proxy.policies.anthropic.simple_judge.call_judge",
            new_callable=AsyncMock,
            return_value=safe_result,
        ):
            result = await policy_with_rules.on_response(response, ctx)

        result_text = cast(AnthropicTextBlock, result["content"][0])
        assert result_text["text"] == "The weather is sunny!"

    @pytest.mark.asyncio
    async def test_on_response_blocks_unsafe_content(self, policy_with_rules):
        """on_response replaces text with blocked message when content violates rules."""
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
            "luthien_proxy.policies.anthropic.simple_judge.call_judge",
            new_callable=AsyncMock,
            return_value=unsafe_result,
        ):
            result = await policy_with_rules.on_response(response, ctx)

        result_text = cast(AnthropicTextBlock, result["content"][0])
        assert "[Content blocked" in result_text["text"]
        assert "dangerous command" in result_text["text"]


class TestAnthropicSimpleJudgePolicyToolUse:
    """Tests for tool use evaluation."""

    @pytest.fixture
    def policy_with_rules(self) -> AnthropicSimpleJudgePolicy:
        """Create a policy subclass with rules for testing."""

        class TestPolicy(AnthropicSimpleJudgePolicy):
            RULES = ["Never allow file deletion", "Block destructive operations"]

        return TestPolicy(block_threshold=0.7)

    @pytest.mark.asyncio
    async def test_on_response_evaluates_tool_use(self, policy_with_rules):
        """on_response evaluates tool_use blocks with judge."""
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
            "luthien_proxy.policies.anthropic.simple_judge.call_judge",
            new_callable=AsyncMock,
            return_value=safe_result,
        ):
            result = await policy_with_rules.on_response(response, ctx)

        result_tool = cast(AnthropicToolUseBlock, result["content"][0])
        assert result_tool["input"] == {"path": "/etc/passwd"}

    @pytest.mark.asyncio
    async def test_on_response_blocks_unsafe_tool_use(self, policy_with_rules):
        """on_response replaces tool input when tool use violates rules."""
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
            "luthien_proxy.policies.anthropic.simple_judge.call_judge",
            new_callable=AsyncMock,
            return_value=unsafe_result,
        ):
            result = await policy_with_rules.on_response(response, ctx)

        result_tool = cast(AnthropicToolUseBlock, result["content"][0])
        assert "error" in result_tool["input"]
        assert "blocked" in str(result_tool["input"]["error"]).lower()

    @pytest.mark.asyncio
    async def test_on_response_evaluates_mixed_content(self, policy_with_rules):
        """on_response evaluates both text and tool_use blocks."""
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
            "luthien_proxy.policies.anthropic.simple_judge.call_judge",
            new_callable=AsyncMock,
            return_value=safe_result,
        ) as mock_judge:
            await policy_with_rules.on_response(response, ctx)

        # Should be called twice: once for text content, once for tool use
        assert mock_judge.call_count == 2


class TestAnthropicSimpleJudgePolicyStreamEvent:
    """Tests for on_stream_event behavior."""

    @pytest.fixture
    def policy_with_rules(self) -> AnthropicSimpleJudgePolicy:
        """Create a policy subclass with rules for testing."""

        class TestPolicy(AnthropicSimpleJudgePolicy):
            RULES = ["Never allow dangerous content"]

        return TestPolicy()

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_text_delta(self, policy_with_rules):
        """on_stream_event passes through text_delta events unchanged."""
        ctx = PolicyContext.for_testing()

        text_delta = TextDelta.model_construct(type="text_delta", text="Hello world")
        event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=text_delta,
        )

        result = await policy_with_rules.on_stream_event(event, ctx)

        assert result is event

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_message_start(self, policy_with_rules):
        """on_stream_event passes through message_start events unchanged."""
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

        result = await policy_with_rules.on_stream_event(event, ctx)

        assert result is event

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_content_block_start(self, policy_with_rules):
        """on_stream_event passes through content_block_start events unchanged."""
        ctx = PolicyContext.for_testing()

        event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=TextBlock.model_construct(type="text", text=""),
        )

        result = await policy_with_rules.on_stream_event(event, ctx)

        assert result is event

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_content_block_stop(self, policy_with_rules):
        """on_stream_event passes through content_block_stop events unchanged."""
        ctx = PolicyContext.for_testing()

        event = RawContentBlockStopEvent.model_construct(
            type="content_block_stop",
            index=0,
        )

        result = await policy_with_rules.on_stream_event(event, ctx)

        assert result is event

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_message_delta(self, policy_with_rules):
        """on_stream_event passes through message_delta events unchanged."""
        ctx = PolicyContext.for_testing()

        event = RawMessageDeltaEvent.model_construct(
            type="message_delta",
            delta={"stop_reason": "end_turn", "stop_sequence": None},
            usage={"output_tokens": 10},
        )

        result = await policy_with_rules.on_stream_event(event, ctx)

        assert result is event

    @pytest.mark.asyncio
    async def test_on_stream_event_passes_through_message_stop(self, policy_with_rules):
        """on_stream_event passes through message_stop events unchanged."""
        ctx = PolicyContext.for_testing()

        event = RawMessageStopEvent.model_construct(type="message_stop")

        result = await policy_with_rules.on_stream_event(event, ctx)

        assert result is event

    @pytest.mark.asyncio
    async def test_on_stream_event_never_returns_none(self, policy_with_rules):
        """on_stream_event never filters out events."""
        ctx = PolicyContext.for_testing()

        events = [
            RawMessageStartEvent.model_construct(
                type="message_start",
                message={
                    "id": "msg_123",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": "claude-sonnet-4-20250514",
                    "stop_reason": None,
                    "usage": {"input_tokens": 10, "output_tokens": 0},
                },
            ),
            RawContentBlockStartEvent.model_construct(
                type="content_block_start",
                index=0,
                content_block=TextBlock.model_construct(type="text", text=""),
            ),
            RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta",
                index=0,
                delta=TextDelta.model_construct(type="text_delta", text="Hi"),
            ),
            RawContentBlockStopEvent.model_construct(type="content_block_stop", index=0),
            RawMessageDeltaEvent.model_construct(
                type="message_delta",
                delta={"stop_reason": "end_turn", "stop_sequence": None},
                usage={"output_tokens": 1},
            ),
            RawMessageStopEvent.model_construct(type="message_stop"),
        ]

        for event in events:
            result = await policy_with_rules.on_stream_event(event, ctx)
            assert result is not None, f"Event of type {event.type} was filtered out"


class TestAnthropicSimpleJudgePolicyJudgeInstructions:
    """Tests for judge instructions generation."""

    def test_judge_instructions_without_rules(self):
        """judge_instructions returns default when no rules defined."""
        policy = AnthropicSimpleJudgePolicy()
        instructions = policy.judge_instructions

        assert "No specific rules defined" in instructions

    def test_judge_instructions_with_rules(self):
        """judge_instructions includes all rules."""

        class TestPolicy(AnthropicSimpleJudgePolicy):
            RULES = ["Rule one", "Rule two", "Rule three"]

        policy = TestPolicy()
        instructions = policy.judge_instructions

        assert "Rule one" in instructions
        assert "Rule two" in instructions
        assert "Rule three" in instructions
        assert "probability" in instructions
        assert "explanation" in instructions


class TestAnthropicSimpleJudgePolicyContentExtraction:
    """Tests for content extraction helper methods."""

    def test_extract_request_content_simple_string(self):
        """_extract_request_content handles simple string content."""
        policy = AnthropicSimpleJudgePolicy()

        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello world"}],
            "max_tokens": 100,
        }

        content = policy._extract_request_content(request)

        assert "user: Hello world" in content

    def test_extract_request_content_with_blocks(self):
        """_extract_request_content handles content block arrays."""
        policy = AnthropicSimpleJudgePolicy()

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
        policy = AnthropicSimpleJudgePolicy()

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
        policy = AnthropicSimpleJudgePolicy()

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
        policy = AnthropicSimpleJudgePolicy()

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
        policy = AnthropicSimpleJudgePolicy()

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


class TestAnthropicSimpleJudgePolicyEventRecording:
    """Tests for policy event recording."""

    @pytest.fixture
    def policy_with_rules(self) -> AnthropicSimpleJudgePolicy:
        """Create a policy subclass with rules for testing."""

        class TestPolicy(AnthropicSimpleJudgePolicy):
            RULES = ["Test rule"]

        return TestPolicy()

    @pytest.mark.asyncio
    async def test_on_request_records_evaluation_event(self, policy_with_rules):
        """on_request records evaluation event to context."""
        from unittest.mock import Mock

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
            "luthien_proxy.policies.anthropic.simple_judge.call_judge",
            new_callable=AsyncMock,
            return_value=safe_result,
        ):
            await policy_with_rules.on_request(request, ctx)

        ctx.record_event.assert_called_once()
        call_args = ctx.record_event.call_args
        event_type = call_args[0][0]
        event_data = call_args[0][1]
        assert event_type == "policy.anthropic_simple_judge.request_evaluated"
        assert event_data["probability"] == 0.3
        assert event_data["explanation"] == "Safe request"
        assert event_data["blocked"] is False

    @pytest.mark.asyncio
    async def test_on_response_records_evaluation_event(self, policy_with_rules):
        """on_response records evaluation event to context."""
        from unittest.mock import Mock

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
            "luthien_proxy.policies.anthropic.simple_judge.call_judge",
            new_callable=AsyncMock,
            return_value=safe_result,
        ):
            await policy_with_rules.on_response(response, ctx)

        ctx.record_event.assert_called_once()
        call_args = ctx.record_event.call_args
        event_type = call_args[0][0]
        event_data = call_args[0][1]
        assert event_type == "policy.anthropic_simple_judge.response_evaluated"
        assert event_data["probability"] == 0.2
        assert event_data["blocked"] is False


__all__ = [
    "TestAnthropicSimpleJudgePolicyProtocol",
    "TestAnthropicSimpleJudgePolicyNoRules",
    "TestAnthropicSimpleJudgePolicyWithRules",
    "TestAnthropicSimpleJudgePolicyToolUse",
    "TestAnthropicSimpleJudgePolicyStreamEvent",
    "TestAnthropicSimpleJudgePolicyJudgeInstructions",
    "TestAnthropicSimpleJudgePolicyContentExtraction",
    "TestAnthropicSimpleJudgePolicyEventRecording",
]
