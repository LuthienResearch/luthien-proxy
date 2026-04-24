"""Unit tests for ToolCallJudgePolicy streaming event handling.

Tests the Anthropic streaming hook methods that buffer tool_use input deltas,
call the judge, and emit the correct event sequences. Judge calls are mocked
— the utils tests cover actual judge invocation.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    TextBlock,
    TextDelta,
    ToolUseBlock,
)
from tests.luthien_proxy.unit_tests.policies.anthropic_event_builders import (
    block_stop,
    event_types,
    message_delta,
    text_delta,
    text_start,
    tool_delta,
    tool_start,
)

from luthien_proxy.policies.tool_call_judge_policy import (
    ToolCallJudgeConfig,
    ToolCallJudgePolicy,
)
from luthien_proxy.policies.tool_call_judge_utils import JudgeResult
from luthien_proxy.policy_core.policy_context import PolicyContext

# ============================================================================
# Helpers
# ============================================================================


def _make_policy(**overrides) -> ToolCallJudgePolicy:
    """Create a ToolCallJudgePolicy with optional config overrides."""
    overrides.setdefault("auth_provider", "user_credentials")
    config = ToolCallJudgeConfig(**overrides)
    return ToolCallJudgePolicy(config)


def _make_context() -> PolicyContext:
    """Create a fresh PolicyContext for testing."""
    return PolicyContext.for_testing(transaction_id="test-txn")


# ============================================================================
# Streaming Tool PassThrough
# ============================================================================


class TestStreamingToolPassThrough:
    """Test streaming event handling for allowed tool_use blocks."""

    @pytest.mark.asyncio
    async def test_tool_start_suppressed(self):
        """tool_start for tool_use block returns [] (buffered)."""
        policy = _make_policy()
        ctx = _make_context()

        start_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
        assert start_events == []

    @pytest.mark.asyncio
    async def test_tool_delta_buffered(self):
        """input_json_delta for buffered tool returns []."""
        policy = _make_policy()
        ctx = _make_context()

        # Start is suppressed
        await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)

        # Delta is buffered
        delta_events = await policy.on_anthropic_stream_event(
            cast(MessageStreamEvent, tool_delta('{"command":"echo hi"}', 0)), ctx
        )
        assert delta_events == []

    @pytest.mark.asyncio
    async def test_tool_allowed_emits_full_block(self):
        """on block_stop, allowed tool emits start + json_delta + stop."""
        policy = _make_policy()
        ctx = _make_context()

        with patch.object(policy, "_evaluate_and_maybe_block_anthropic", new_callable=AsyncMock) as mock_eval:
            mock_eval.return_value = None  # allowed

            # Start is suppressed
            await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, tool_start(0, tool_id="toolu_123", name="Bash")), ctx
            )

            # Delta is buffered
            await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, tool_delta('{"command":"echo hello"}', 0)), ctx
            )

            # Stop triggers reconstruction
            stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)

            assert event_types(stop_events) == ["content_block_start", "content_block_delta", "content_block_stop"]

            # Verify reconstructed ToolUseBlock has correct id/name
            start_event = stop_events[0]
            assert isinstance(start_event, RawContentBlockStartEvent)
            assert isinstance(start_event.content_block, ToolUseBlock)
            assert start_event.content_block.id == "toolu_123"
            assert start_event.content_block.name == "Bash"

            # Verify buffered JSON is in delta
            delta_event = stop_events[1]
            assert isinstance(delta_event, RawContentBlockDeltaEvent)
            assert isinstance(delta_event.delta, InputJSONDelta)
            assert delta_event.delta.partial_json == '{"command":"echo hello"}'


# ============================================================================
# Streaming Tool Blocked
# ============================================================================


class TestStreamingToolBlocked:
    """Test streaming event handling for blocked tool_use blocks."""

    @pytest.mark.asyncio
    async def test_tool_blocked_emits_text_replacement(self):
        """blocked tool emits text start + text_delta + stop (not a bare stop)."""
        policy = _make_policy()
        ctx = _make_context()

        judge_result = JudgeResult(probability=0.9, explanation="dangerous operation", prompt=[], response_text="")

        with patch.object(policy, "_evaluate_and_maybe_block_anthropic", new_callable=AsyncMock) as mock_eval:
            mock_eval.return_value = judge_result

            # Start is suppressed
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0, name="Bash")), ctx)

            # Delta is buffered
            await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, tool_delta('{"command":"rm -rf /"}', 0)), ctx
            )

            # Stop returns text block replacement
            stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)

            assert event_types(stop_events) == ["content_block_start", "content_block_delta", "content_block_stop"]

            # Verify it's a text block
            start_event = stop_events[0]
            assert isinstance(start_event, RawContentBlockStartEvent)
            assert isinstance(start_event.content_block, TextBlock)

            # Verify blocked message contains tool name and explanation
            delta_event = stop_events[1]
            assert isinstance(delta_event, RawContentBlockDeltaEvent)
            assert isinstance(delta_event.delta, TextDelta)
            assert "Bash" in delta_event.delta.text
            assert "dangerous operation" in delta_event.delta.text

    @pytest.mark.asyncio
    async def test_blocked_block_index_tracked(self):
        """after blocking, the index is in _anthropic_blocked_blocks(context)."""
        policy = _make_policy()
        ctx = _make_context()

        judge_result = JudgeResult(probability=0.9, explanation="blocked", prompt=[], response_text="")

        with patch.object(policy, "_evaluate_and_maybe_block_anthropic", new_callable=AsyncMock) as mock_eval:
            mock_eval.return_value = judge_result

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"x":1}', 0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)

            # Verify index 0 is in blocked set
            blocked_blocks = policy._anthropic_blocked_blocks(ctx)
            assert 0 in blocked_blocks


# ============================================================================
# Streaming Text PassThrough
# ============================================================================


class TestStreamingTextPassThrough:
    """Test streaming event handling for text blocks."""

    @pytest.mark.asyncio
    async def test_text_start_passes_through(self):
        """text block start event returned as-is."""
        policy = _make_policy()
        ctx = _make_context()

        start_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, text_start(0)), ctx)
        assert event_types(start_events) == ["content_block_start"]

    @pytest.mark.asyncio
    async def test_text_delta_passes_through(self):
        """text delta for non-buffered index returned as-is."""
        policy = _make_policy()
        ctx = _make_context()

        # text_start at index 0 (not buffered)
        await policy.on_anthropic_stream_event(cast(MessageStreamEvent, text_start(0)), ctx)

        # text_delta at index 0 passes through
        delta_events = await policy.on_anthropic_stream_event(
            cast(MessageStreamEvent, text_delta("hello world", 0)), ctx
        )
        assert len(delta_events) == 1
        assert delta_events[0].type == "content_block_delta"

    @pytest.mark.asyncio
    async def test_non_tool_stop_passes_through(self):
        """block_stop for non-buffered index returned as-is."""
        policy = _make_policy()
        ctx = _make_context()

        # Process text block (not buffered for tool)
        await policy.on_anthropic_stream_event(cast(MessageStreamEvent, text_start(0)), ctx)
        await policy.on_anthropic_stream_event(cast(MessageStreamEvent, text_delta("hello", 0)), ctx)

        # Stop should pass through
        stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)
        assert len(stop_events) == 1
        assert stop_events[0].type == "content_block_stop"


# ============================================================================
# Streaming Unknown Event
# ============================================================================


class TestStreamingUnknownEvent:
    """Test streaming event handling for unknown event types."""

    @pytest.mark.asyncio
    async def test_unknown_event_passes_through(self):
        """any other MessageStreamEvent type returns [event]."""
        policy = _make_policy()
        ctx = _make_context()

        # message_delta is not a content_block event
        msg_event = message_delta("end_turn")
        result = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, msg_event), ctx)
        assert result == [msg_event]


# ============================================================================
# Streaming Judge Failure
# ============================================================================


class TestStreamingJudgeFailure:
    """Test fail-secure behavior when judge fails."""

    @pytest.mark.asyncio
    async def test_judge_exception_returns_high_probability(self):
        """When _evaluate_and_maybe_block_anthropic returns high-probability result, tool is blocked."""
        policy = _make_policy()
        ctx = _make_context()

        # Simulate judge failure: internal error handling returns high-prob result
        judge_result = JudgeResult(
            probability=1.0,
            explanation="Judge evaluation failed: connection timeout",
            prompt=[],
            response_text="",
        )

        with patch.object(policy, "_evaluate_and_maybe_block_anthropic", new_callable=AsyncMock) as mock_eval:
            mock_eval.return_value = judge_result

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"cmd":"dangerous"}', 0)), ctx)

            stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)

            # Tool is blocked (replaced with text)
            assert event_types(stop_events) == ["content_block_start", "content_block_delta", "content_block_stop"]
            delta = stop_events[1]
            assert isinstance(delta, RawContentBlockDeltaEvent)
            assert "evaluation failed" in delta.delta.text.lower()


# ============================================================================
# Streaming Multiple Tools
# ============================================================================


class TestStreamingMultipleTools:
    """Test mixed tool blocks, some allowed and some blocked."""

    @pytest.mark.asyncio
    async def test_mixed_tools_one_allowed_one_blocked(self):
        """Two tool blocks at different indices, one allowed and one blocked."""
        policy = _make_policy()
        ctx = _make_context()

        # First tool: allowed
        judge_result_allowed = None
        # Second tool: blocked
        judge_result_blocked = JudgeResult(probability=0.8, explanation="harmful", prompt=[], response_text="")

        with patch.object(policy, "_evaluate_and_maybe_block_anthropic", new_callable=AsyncMock) as mock_eval:
            mock_eval.side_effect = [judge_result_allowed, judge_result_blocked]

            # Tool at index 0 — allowed
            await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, tool_start(0, tool_id="toolu_1", name="Tool1")), ctx
            )
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"param":"value"}', 0)), ctx)
            stop_0 = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)

            # Tool at index 0 was allowed, so we get full reconstruction
            assert event_types(stop_0) == ["content_block_start", "content_block_delta", "content_block_stop"]
            start_0 = stop_0[0]
            assert isinstance(start_0, RawContentBlockStartEvent)
            assert isinstance(start_0.content_block, ToolUseBlock)
            assert start_0.content_block.name == "Tool1"

            # Tool at index 1 — blocked
            await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, tool_start(1, tool_id="toolu_2", name="Tool2")), ctx
            )
            await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, tool_delta('{"param":"dangerous"}', 1)), ctx
            )
            stop_1 = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(1)), ctx)

            # Tool at index 1 was blocked, so we get text replacement
            assert event_types(stop_1) == ["content_block_start", "content_block_delta", "content_block_stop"]
            start_1 = stop_1[0]
            assert isinstance(start_1, RawContentBlockStartEvent)
            assert isinstance(start_1.content_block, TextBlock)
            delta_1 = stop_1[1]
            assert isinstance(delta_1.delta, TextDelta)
            assert "Tool2" in delta_1.delta.text


# ============================================================================
# Non-Streaming Response
# ============================================================================


class TestNonStreamingResponse:
    """Test on_anthropic_response (non-streaming path).

    Note: streaming stop_reason correction is not tested here because
    ToolCallJudgePolicy doesn't handle message_delta events — they pass
    through to the parent policy. That path is covered by
    test_simple_llm_policy.py::TestStopReasonCorrection.
    """

    @pytest.mark.asyncio
    async def test_empty_content_returns_unchanged(self):
        """response with empty content list returns as-is."""
        policy = _make_policy()
        ctx = _make_context()

        response: dict[str, Any] = {
            "content": [],
            "stop_reason": "end_turn",
        }

        result = await policy.on_anthropic_response(response, ctx)
        assert result == response

    @pytest.mark.asyncio
    async def test_text_only_returns_unchanged(self):
        """response with only text blocks returns as-is."""
        policy = _make_policy()
        ctx = _make_context()

        response: dict[str, Any] = {
            "content": [{"type": "text", "text": "hello world"}],
            "stop_reason": "end_turn",
        }

        result = await policy.on_anthropic_response(response, ctx)
        assert result == response

    @pytest.mark.asyncio
    async def test_tool_allowed_stays_in_content(self):
        """tool_use block below threshold stays in content."""
        policy = _make_policy(probability_threshold=0.8)
        ctx = _make_context()

        response: dict[str, Any] = {
            "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "echo hi"}}],
            "stop_reason": "tool_use",
        }

        with patch.object(policy, "_evaluate_and_maybe_block_anthropic", new_callable=AsyncMock) as mock_eval:
            mock_eval.return_value = None  # allowed

            result = await policy.on_anthropic_response(response, ctx)

        # Tool call stays in content
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "tool_use"
        assert result["content"][0]["name"] == "Bash"

    @pytest.mark.asyncio
    async def test_tool_blocked_replaced_with_text(self):
        """tool_use replaced with text block containing blocked message."""
        policy = _make_policy()
        ctx = _make_context()

        response: dict[str, Any] = {
            "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "rm -rf /"}}],
            "stop_reason": "tool_use",
        }

        judge_result = JudgeResult(probability=0.9, explanation="destructive", prompt=[], response_text="")

        with patch.object(policy, "_evaluate_and_maybe_block_anthropic", new_callable=AsyncMock) as mock_eval:
            mock_eval.return_value = judge_result

            result = await policy.on_anthropic_response(response, ctx)

        # Tool call replaced with text
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        assert "Bash" in result["content"][0]["text"]
        assert "destructive" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_all_tools_blocked_stop_reason_corrected(self):
        """when all tool_use blocks blocked, stop_reason changes from tool_use to end_turn."""
        policy = _make_policy()
        ctx = _make_context()

        response: dict[str, Any] = {
            "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "rm /"}}],
            "stop_reason": "tool_use",
        }

        judge_result = JudgeResult(probability=0.9, explanation="blocked", prompt=[], response_text="")

        with patch.object(policy, "_evaluate_and_maybe_block_anthropic", new_callable=AsyncMock) as mock_eval:
            mock_eval.return_value = judge_result

            result = await policy.on_anthropic_response(response, ctx)

        assert result["stop_reason"] == "end_turn"

    @pytest.mark.asyncio
    async def test_some_tools_blocked_stop_reason_kept(self):
        """when some tool_use blocks remain, stop_reason stays tool_use."""
        policy = _make_policy()
        ctx = _make_context()

        response: dict[str, Any] = {
            "content": [
                {"type": "tool_use", "id": "toolu_1", "name": "Tool1", "input": {"x": 1}},
                {"type": "tool_use", "id": "toolu_2", "name": "Tool2", "input": {"x": 2}},
            ],
            "stop_reason": "tool_use",
        }

        # First tool allowed, second blocked
        with patch.object(policy, "_evaluate_and_maybe_block_anthropic", new_callable=AsyncMock) as mock_eval:
            mock_eval.side_effect = [
                None,  # Tool1 allowed
                JudgeResult(probability=0.9, explanation="blocked", prompt=[], response_text=""),  # Tool2 blocked
            ]

            result = await policy.on_anthropic_response(response, ctx)

        # Some tool_use remains, so stop_reason stays
        assert result["stop_reason"] == "tool_use"

    @pytest.mark.asyncio
    async def test_mixed_text_and_tool(self):
        """text block untouched, tool_use evaluated and potentially blocked."""
        policy = _make_policy()
        ctx = _make_context()

        response: dict[str, Any] = {
            "content": [
                {"type": "text", "text": "About to call tool:"},
                {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "rm /"}},
            ],
            "stop_reason": "tool_use",
        }

        judge_result = JudgeResult(probability=0.9, explanation="destructive", prompt=[], response_text="")

        with patch.object(policy, "_evaluate_and_maybe_block_anthropic", new_callable=AsyncMock) as mock_eval:
            mock_eval.return_value = judge_result

            result = await policy.on_anthropic_response(response, ctx)

        # Text stays, tool replaced
        assert len(result["content"]) == 2
        assert result["content"][0]["type"] == "text"
        assert result["content"][0]["text"] == "About to call tool:"
        assert result["content"][1]["type"] == "text"
        assert "Bash" in result["content"][1]["text"]


# ============================================================================
# State Cleanup
# ============================================================================


class TestStateCleanup:
    """Test request-scoped state cleanup."""

    @pytest.mark.asyncio
    async def test_streaming_policy_complete_pops_state(self):
        """after calling on_anthropic_streaming_policy_complete, old state is removed."""
        policy = _make_policy()
        ctx = _make_context()

        # Buffer a tool to create non-empty state
        await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)

        state_before = policy._anthropic_state(ctx)
        assert 0 in state_before.buffered_tool_uses

        # Clean up
        await policy.on_anthropic_streaming_policy_complete(ctx)

        # get_request_state returns a new object — the old one was removed
        state_after = policy._anthropic_state(ctx)
        assert state_after is not state_before
        assert 0 not in state_after.buffered_tool_uses


# ============================================================================
# Blocked Message Formatting
# ============================================================================


class TestBlockedMessageFormatting:
    """Test blocked message template formatting."""

    def test_default_template_formatting(self):
        """verify default template includes tool name, arguments, probability, explanation."""
        policy = _make_policy()

        tool_call = {
            "name": "Bash",
            "arguments": '{"command":"rm -rf /"}',
        }
        judge_result = JudgeResult(probability=0.95, explanation="destructive operation", prompt=[], response_text="")

        message = policy._format_anthropic_blocked_message(tool_call, judge_result)

        assert "Bash" in message
        assert "rm -rf /" in message
        assert "0.95" in message
        assert "destructive operation" in message

    def test_custom_template(self):
        """policy with custom blocked_message_template uses it."""
        policy = _make_policy(blocked_message_template="BLOCKED: {tool_name} with probability {probability:.1f}")

        tool_call = {"name": "Python", "arguments": "{}"}
        judge_result = JudgeResult(probability=0.75, explanation="explanation", prompt=[], response_text="")

        message = policy._format_anthropic_blocked_message(tool_call, judge_result)

        assert message == "BLOCKED: Python with probability 0.8"

    def test_long_arguments_truncated(self):
        """arguments longer than TOOL_ARGS_TRUNCATION_LENGTH are truncated."""
        from luthien_proxy.utils.constants import TOOL_ARGS_TRUNCATION_LENGTH

        policy = _make_policy()

        long_args = '{"data":"' + "x" * 10000 + '"}'
        tool_call = {"name": "Tool", "arguments": long_args}
        judge_result = JudgeResult(probability=0.5, explanation="test", prompt=[], response_text="")

        message = policy._format_anthropic_blocked_message(tool_call, judge_result)

        # Truncated prefix appears in message, but full args do not
        assert long_args[:TOOL_ARGS_TRUNCATION_LENGTH] in message
        assert long_args not in message

    def test_template_with_empty_explanation(self):
        """format handles empty explanation via 'or' fallback."""
        policy = _make_policy()

        tool_call = {"name": "Tool", "arguments": "{}"}
        judge_result = JudgeResult(probability=0.9, explanation="", prompt=[], response_text="")

        message = policy._format_anthropic_blocked_message(tool_call, judge_result)

        assert "Tool" in message
        assert "No explanation provided" in message


# ============================================================================
# Anthropic Hook Configuration
# ============================================================================


class TestPolicyConfiguration:
    """Test policy initialization and configuration."""

    def test_policy_initialization_with_defaults(self):
        """policy initializes with default config."""
        policy = _make_policy()

        assert policy.short_policy_name == "ToolJudge"
        assert policy.config.probability_threshold == 0.6
        assert policy.config.model == "claude-haiku-4-5"

    def test_policy_initialization_with_overrides(self):
        """policy initializes with custom config."""
        policy = _make_policy(probability_threshold=0.5, model="gpt-4o")

        assert policy.config.probability_threshold == 0.5
        assert policy.config.model == "gpt-4o"

    def test_judge_instructions_customizable(self):
        """policy accepts custom judge instructions."""
        custom_instructions = "You are a custom judge with special rules"
        policy = _make_policy(judge_instructions=custom_instructions)

        assert policy._judge_instructions == custom_instructions
