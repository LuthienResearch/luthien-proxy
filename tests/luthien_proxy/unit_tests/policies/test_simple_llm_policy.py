"""Unit tests for SimpleLLMPolicy streaming event handling.

Tests the Anthropic streaming hook methods that buffer content blocks,
call the judge, and emit the correct event sequences. Judge calls are
mocked — the utils tests cover actual judge invocation.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawMessageDeltaEvent,
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

from luthien_proxy.policies.simple_llm_policy import (
    JUDGE_ERROR_BLOCKED_MESSAGE,
    JUDGE_UNAVAILABLE_WARNING,
    SimpleLLMPolicy,
)
from luthien_proxy.policies.simple_llm_utils import (
    JudgeAction,
    ReplacementBlock,
    SimpleLLMJudgeConfig,
)
from luthien_proxy.policy_core.policy_context import PolicyContext

# ============================================================================
# Helpers
# ============================================================================


def _make_policy(on_error: str = "block") -> SimpleLLMPolicy:
    config = SimpleLLMJudgeConfig(
        instructions="test instructions",
        on_error=on_error,
        auth_provider="user_credentials",
    )
    return SimpleLLMPolicy(config)


def _make_context() -> PolicyContext:
    return PolicyContext.for_testing(transaction_id="test-txn")


# ============================================================================
# Text block streaming
# ============================================================================


class TestTextBlockStreaming:
    """Test streaming event handling for text blocks."""

    @pytest.mark.asyncio
    async def test_text_pass_through(self):
        """Text block judged 'pass' emits: start + delta + stop together."""
        policy = _make_policy()
        ctx = _make_context()

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="pass")

            # Start is buffered (not emitted immediately)
            start_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, text_start(0)), ctx)
            assert start_events == []

            # Delta is buffered
            delta_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, text_delta("hello world", 0)), ctx
            )
            assert delta_events == []

            # Stop triggers judge, emits buffered start + delta + stop
            stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)
            assert event_types(stop_events) == ["content_block_start", "content_block_delta", "content_block_stop"]
            # Verify the buffered text is in the delta
            delta = stop_events[1]
            assert isinstance(delta, RawContentBlockDeltaEvent)
            assert isinstance(delta.delta, TextDelta)
            assert delta.delta.text == "hello world"

    @pytest.mark.asyncio
    async def test_text_blocked(self):
        """Text block judged 'block' suppresses entirely (no start, no stop)."""
        policy = _make_policy()
        ctx = _make_context()

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="block")

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, text_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, text_delta("secret", 0)), ctx)

            stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)
            # Text start was buffered — judge blocked, so entire block is suppressed
            assert stop_events == []

    @pytest.mark.asyncio
    async def test_text_replaced_with_text(self):
        """Text block judged 'replace' emits replacement start + delta + stop."""
        policy = _make_policy()
        ctx = _make_context()

        replacement = ReplacementBlock(type="text", text="[REDACTED]")
        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="replace", blocks=(replacement,))

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, text_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, text_delta("secret", 0)), ctx)

            stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)
            assert event_types(stop_events) == [
                "content_block_start",
                "content_block_delta",
                "content_block_stop",
            ]


# ============================================================================
# Tool block streaming
# ============================================================================


class TestToolBlockStreaming:
    """Test streaming event handling for tool_use blocks."""

    @pytest.mark.asyncio
    async def test_tool_pass_through(self):
        """Tool block judged 'pass' emits: start + delta + stop."""
        policy = _make_policy()
        ctx = _make_context()

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="pass")

            # Start is suppressed (buffered for judge)
            start_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            assert start_events == []

            # Delta is buffered
            delta_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, tool_delta('{"command":"echo hi"}', 0)), ctx
            )
            assert delta_events == []

            # Stop triggers judge, emits full tool block
            stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)
            assert event_types(stop_events) == [
                "content_block_start",
                "content_block_delta",
                "content_block_stop",
            ]
            # Verify reconstructed tool block
            start = stop_events[0]
            assert isinstance(start, RawContentBlockStartEvent)
            assert isinstance(start.content_block, ToolUseBlock)
            assert start.content_block.name == "Bash"

    @pytest.mark.asyncio
    async def test_tool_blocked_no_orphaned_stop(self):
        """BUG FIX: Tool block judged 'block' must emit nothing — start was suppressed.

        Before the fix, this returned [content_block_stop] without a preceding
        content_block_start, violating the Anthropic streaming protocol.
        """
        policy = _make_policy()
        ctx = _make_context()

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="block")

            # Start suppressed
            start_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            assert start_events == []

            # Delta buffered
            await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, tool_delta('{"command":"rm -rf /"}', 0)), ctx
            )

            # Stop: blocked tool emits a text block explaining the block
            stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)
            assert event_types(stop_events) == [
                "content_block_start",
                "content_block_delta",
                "content_block_stop",
            ], f"Blocked tool_use should emit explanatory text block, got: {event_types(stop_events)}"
            # Verify the text explains what was blocked
            delta = [e for e in stop_events if isinstance(e, RawContentBlockDeltaEvent)][0]
            assert isinstance(delta.delta, TextDelta)
            assert "Bash" in delta.delta.text
            assert "blocked" in delta.delta.text

    @pytest.mark.asyncio
    async def test_tool_replaced_with_text(self):
        """Tool block judged 'replace' with text emits text block events."""
        policy = _make_policy()
        ctx = _make_context()

        replacement = ReplacementBlock(type="text", text="Tool call blocked by policy")
        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="replace", blocks=(replacement,))

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, tool_delta('{"command":"echo hi"}', 0)), ctx
            )

            stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)
            assert event_types(stop_events) == [
                "content_block_start",
                "content_block_delta",
                "content_block_stop",
            ]
            # Verify it's a text replacement, not tool_use
            start = stop_events[0]
            assert isinstance(start, RawContentBlockStartEvent)
            assert isinstance(start.content_block, TextBlock)


# ============================================================================
# Multi-block sequences
# ============================================================================


class TestMultiBlockStreaming:
    """Test mixed block sequences to verify index tracking and stop_reason."""

    @pytest.mark.asyncio
    async def test_text_passes_then_tool_blocked(self):
        """Text block passes, then tool block is blocked — correct events and stop_reason."""
        policy = _make_policy(on_error="block")
        ctx = _make_context()

        pass_action = JudgeAction(action="pass")
        block_action = JudgeAction(action="block")

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.side_effect = [pass_action, block_action]

            # Text block at index 0 — passes through (start is buffered until stop)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, text_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, text_delta("hello", 0)), ctx)
            text_stop = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)
            assert event_types(text_stop) == ["content_block_start", "content_block_delta", "content_block_stop"]

            # Tool block at index 1 — blocked, emits explanatory text
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(1)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta("{}", 1)), ctx)
            tool_stop = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(1)), ctx)
            assert event_types(tool_stop) == [
                "content_block_start",
                "content_block_delta",
                "content_block_stop",
            ]

            # stop_reason should be end_turn (no tool_use passed through)
            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )
            delta_event = [e for e in msg_events if isinstance(e, RawMessageDeltaEvent)][0]
            assert delta_event.delta.stop_reason == "end_turn"


# ============================================================================
# Judge failure behavior
# ============================================================================


class TestJudgeFailure:
    """Test on_error='pass' and on_error='block' when judge fails."""

    @pytest.mark.asyncio
    async def test_judge_failure_block_tool_suppressed(self):
        """on_error='block' + judge failure on tool_use: nothing emitted."""
        policy = _make_policy(on_error="block")
        ctx = _make_context()

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="block", judge_failed=True)

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"cmd":"x"}', 0)), ctx)

            stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)
            # Blocked tool emits explanatory text block
            assert event_types(stop_events) == [
                "content_block_start",
                "content_block_delta",
                "content_block_stop",
            ]

    @pytest.mark.asyncio
    async def test_judge_failure_pass_tool_emitted_with_warning(self):
        """on_error='pass' + judge failure on tool_use: tool passes through, warning injected at message_delta."""
        policy = _make_policy(on_error="pass")
        ctx = _make_context()

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="pass", judge_failed=True)

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"cmd":"echo"}', 0)), ctx)

            stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)
            # Tool is passed through
            assert event_types(stop_events) == [
                "content_block_start",
                "content_block_delta",
                "content_block_stop",
            ]

            # Warning is injected before message_delta
            msg_delta_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )
            types = event_types(msg_delta_events)
            assert "content_block_start" in types, "Warning block should be injected before message_delta"
            assert types[-1] == "message_delta", "message_delta should be last"

            # Find the warning text
            warning_deltas = [
                e
                for e in msg_delta_events
                if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, TextDelta)
            ]
            assert any(JUDGE_UNAVAILABLE_WARNING in d.delta.text for d in warning_deltas)


# ============================================================================
# stop_reason correction
# ============================================================================


class TestStopReasonCorrection:
    """Test that stop_reason is corrected when tool_use blocks are blocked."""

    @pytest.mark.asyncio
    async def test_stop_reason_corrected_after_tool_blocked(self):
        """When all tool_use blocks are blocked, stop_reason should be 'end_turn' not 'tool_use'."""
        policy = _make_policy(on_error="block")
        ctx = _make_context()

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="block")

            # Process a tool block that gets blocked
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta("{}", 0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)

            # message_delta with stop_reason='tool_use' should be corrected to 'end_turn'
            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )
            delta_event = [e for e in msg_events if isinstance(e, RawMessageDeltaEvent)][0]
            assert delta_event.delta.stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_stop_reason_kept_when_tool_passed(self):
        """When tool_use blocks pass through, stop_reason stays 'tool_use'."""
        policy = _make_policy()
        ctx = _make_context()

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="pass")

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"x":1}', 0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)

            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )
            delta_event = [e for e in msg_events if isinstance(e, RawMessageDeltaEvent)][0]
            assert delta_event.delta.stop_reason == "tool_use"


# ============================================================================
# Non-streaming response
# ============================================================================


class TestNonStreamingResponse:
    """Test on_anthropic_response (non-streaming path)."""

    @pytest.mark.asyncio
    async def test_text_pass_through(self):
        policy = _make_policy()
        ctx = _make_context()

        response: dict[str, Any] = {
            "content": [{"type": "text", "text": "hello"}],
            "stop_reason": "end_turn",
        }

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="pass")
            result = await policy.on_anthropic_response(response, ctx)

        assert len(result["content"]) == 1
        assert result["content"][0]["text"] == "hello"

    @pytest.mark.asyncio
    async def test_tool_blocked_non_streaming(self):
        policy = _make_policy(on_error="block")
        ctx = _make_context()

        response: dict[str, Any] = {
            "content": [
                {"type": "tool_use", "id": "toolu_abc", "name": "Bash", "input": {"command": "rm -rf /"}},
            ],
            "stop_reason": "tool_use",
        }

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="block")
            result = await policy.on_anthropic_response(response, ctx)

        # Blocked tool_use is replaced with explanatory text
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        assert "Bash" in result["content"][0]["text"]
        assert "blocked" in result["content"][0]["text"]
        assert result["stop_reason"] == "end_turn"

    @pytest.mark.asyncio
    async def test_tool_blocked_judge_failed_non_streaming(self):
        """on_error='block' + judge failure: blocked message indicates judge unavailable."""
        policy = _make_policy(on_error="block")
        ctx = _make_context()

        response: dict[str, Any] = {
            "content": [
                {"type": "tool_use", "id": "toolu_abc", "name": "Bash", "input": {"command": "echo hi"}},
            ],
            "stop_reason": "tool_use",
        }

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="block", judge_failed=True)
            result = await policy.on_anthropic_response(response, ctx)

        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        assert "Bash" in result["content"][0]["text"]
        assert "policy evaluation unavailable" in result["content"][0]["text"]
        assert result["stop_reason"] == "end_turn"


# ============================================================================
# Judge failure: streaming blocked tool message
# ============================================================================


class TestJudgeFailedBlockedToolStreaming:
    """Test that judge-failed blocked tools use a distinct message in streaming."""

    @pytest.mark.asyncio
    async def test_tool_blocked_judge_failed_streaming(self):
        """on_error='block' + judge failure on tool_use: blocked text says judge unavailable."""
        policy = _make_policy(on_error="block")
        ctx = _make_context()

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="block", judge_failed=True)

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, tool_delta('{"command":"echo hi"}', 0)), ctx
            )

            stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)

        # Should emit a text block with the judge-failed message
        assert event_types(stop_events) == [
            "content_block_start",
            "content_block_delta",
            "content_block_stop",
        ]
        delta = [e for e in stop_events if isinstance(e, RawContentBlockDeltaEvent)][0]
        assert isinstance(delta.delta, TextDelta)
        assert "Bash" in delta.delta.text
        assert "policy evaluation unavailable" in delta.delta.text

    @pytest.mark.asyncio
    async def test_tool_blocked_intentional_uses_standard_message(self):
        """Intentional block (no judge failure) uses the standard blocked message."""
        policy = _make_policy(on_error="block")
        ctx = _make_context()

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="block", judge_failed=False)

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, tool_delta('{"command":"rm -rf /"}', 0)), ctx
            )

            stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)

        delta = [e for e in stop_events if isinstance(e, RawContentBlockDeltaEvent)][0]
        assert isinstance(delta.delta, TextDelta)
        assert "blocked by policy" in delta.delta.text
        assert "policy evaluation unavailable" not in delta.delta.text


# ============================================================================
# Judge failure: empty response injection (the core silent-drop bug)
# ============================================================================


class TestJudgeErrorEmptyResponseInjection:
    @pytest.mark.asyncio
    async def test_text_block_judge_error_non_streaming_injects_error_message(self):
        policy = _make_policy(on_error="block")
        ctx = _make_context()

        response: dict[str, Any] = {
            "content": [{"type": "text", "text": "some response"}],
            "stop_reason": "end_turn",
        }

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="block", judge_failed=True)
            result = await policy.on_anthropic_response(cast(Any, response), ctx)

        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        assert JUDGE_ERROR_BLOCKED_MESSAGE in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_text_block_judge_error_streaming_injects_error_at_message_delta(self):
        policy = _make_policy(on_error="block")
        ctx = _make_context()

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="block", judge_failed=True)

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, text_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, text_delta("hello", 0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)

            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("end_turn")), ctx
            )

        types = event_types(msg_events)
        assert "content_block_start" in types
        assert types[-1] == "message_delta"
        error_deltas = [
            e for e in msg_events if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, TextDelta)
        ]
        assert any(JUDGE_ERROR_BLOCKED_MESSAGE in d.delta.text for d in error_deltas)
