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
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ToolUseBlock,
)
from tests.luthien_proxy.fixtures.anthropic_stream_validator import validate_anthropic_event_ordering
from tests.luthien_proxy.unit_tests.policies.anthropic_event_builders import (
    block_stop,
    event_types,
    full_stream,
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


# ============================================================================
# Multi-block replacement indices: monotonicity + no-collision-with-passthrough
# ============================================================================


class TestMultiBlockReplacementIndices:
    @pytest.mark.asyncio
    async def test_replacement_with_multiple_blocks_uses_monotonic_indices(self):
        """Two replacement blocks must use indices [0, 1], not [0, 0]."""
        policy = _make_policy()
        ctx = _make_context()

        blocks = (
            ReplacementBlock(type="text", text="A"),
            ReplacementBlock(type="text", text="B"),
        )
        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="replace", blocks=blocks)

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"command":"ls"}', 0)), ctx)
            stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)
            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )

        all_events = stop_events + msg_events
        start_indices = [e.index for e in all_events if isinstance(e, RawContentBlockStartEvent)]

        assert len(start_indices) == 2, f"Expected 2 replacement blocks, got {len(start_indices)}"
        assert start_indices[0] < start_indices[1], (
            f"Replacement block indices must be monotonically increasing, got {start_indices}"
        )

        validate_anthropic_event_ordering(full_stream(all_events)).assert_valid()

    @pytest.mark.asyncio
    async def test_replacement_then_passthrough_no_index_collision(self):
        """Replacing one block with 2 then passing the next upstream block must not collide.

        tool@0 → replace([A, B]) emits at [0, 1]; tool@1 → pass must emit at 2 (not 1).
        """
        policy = _make_policy()
        ctx = _make_context()

        replace_action = JudgeAction(
            action="replace",
            blocks=(ReplacementBlock(type="text", text="A"), ReplacementBlock(type="text", text="B")),
        )
        pass_action = JudgeAction(action="pass")

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.side_effect = [replace_action, pass_action]

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"cmd":"ls"}', 0)), ctx)
            replace_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(1)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"cmd":"echo"}', 1)), ctx)
            pass_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(1)), ctx)

            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )

        all_events = replace_events + pass_events + msg_events
        start_indices = [e.index for e in all_events if isinstance(e, RawContentBlockStartEvent)]

        assert len(start_indices) == 3, f"Expected A, B, tool — got {start_indices}"
        assert start_indices == sorted(set(start_indices)), (
            f"Indices must be strictly increasing with no duplicates, got {start_indices}"
        )
        validate_anthropic_event_ordering(full_stream(all_events)).assert_valid()

    @pytest.mark.asyncio
    async def test_thinking_block_passthrough_after_multi_replace_uses_shifted_index(self):
        """Thinking block (passthrough, not buffered) must apply index_shift.

        tool@0 → replace([A, B]) emits at [0, 1] and shifts; subsequent
        thinking@1 must emit at shifted index 2, not collide at 1.
        """
        policy = _make_policy()
        ctx = _make_context()

        replace = JudgeAction(
            action="replace",
            blocks=(ReplacementBlock(type="text", text="A"), ReplacementBlock(type="text", text="B")),
        )
        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = replace

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"x":1}', 0)), ctx)
            replace_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)

            thinking_start = RawContentBlockStartEvent(
                type="content_block_start",
                index=1,
                content_block=ThinkingBlock(type="thinking", thinking="hmm", signature="sig"),
            )
            thinking_stop = RawContentBlockStopEvent(type="content_block_stop", index=1)
            t_start = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, thinking_start), ctx)
            t_stop = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, thinking_stop), ctx)

            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )

        all_events = replace_events + t_start + t_stop + msg_events
        starts = [e.index for e in all_events if isinstance(e, RawContentBlockStartEvent)]
        assert starts == [0, 1, 2], f"Thinking-block passthrough must emit at index+shift=2, got starts {starts}"
        validate_anthropic_event_ordering(full_stream(all_events)).assert_valid()

    @pytest.mark.asyncio
    async def test_two_multi_block_replacements_compound_shift(self):
        """Two multi-block replacements in series must compound index_shift.

        tool@0 → replace([A,B]) → emits [0,1] shift=1
        tool@1 → replace([C,D]) → emits at shifted [2,3] shift=3
        tool@2 → pass → emits at shifted index 4
        Guards against `index_shift = N-1` regression vs `+=`.
        """
        policy = _make_policy()
        ctx = _make_context()

        replace_ab = JudgeAction(
            action="replace",
            blocks=(ReplacementBlock(type="text", text="A"), ReplacementBlock(type="text", text="B")),
        )
        replace_cd = JudgeAction(
            action="replace",
            blocks=(ReplacementBlock(type="text", text="C"), ReplacementBlock(type="text", text="D")),
        )
        pass_action = JudgeAction(action="pass")

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.side_effect = [replace_ab, replace_cd, pass_action]

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"x":1}', 0)), ctx)
            e1 = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(1)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"x":2}', 1)), ctx)
            e2 = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(1)), ctx)

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(2)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"x":3}', 2)), ctx)
            e3 = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(2)), ctx)

            msg = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, message_delta("tool_use")), ctx)

        all_events = e1 + e2 + e3 + msg
        starts = [e.index for e in all_events if isinstance(e, RawContentBlockStartEvent)]
        assert starts == [0, 1, 2, 3, 4], f"Compound shift broken: expected [0,1,2,3,4], got {starts}"
        validate_anthropic_event_ordering(full_stream(all_events)).assert_valid()

    @pytest.mark.asyncio
    async def test_one_for_one_replace_after_multi_block_replace_no_extra_shift(self):
        """1-for-1 replace consumes its upstream slot; no extra shift.

        tool@0 → replace([A,B]) shifts by 1; tool@1 → replace([C]) is 1-for-1
        and must emit at shifted index 2 without bumping shift further;
        tool@2 → pass emits at shifted index 3.
        """
        policy = _make_policy()
        ctx = _make_context()

        replace_ab = JudgeAction(
            action="replace",
            blocks=(ReplacementBlock(type="text", text="A"), ReplacementBlock(type="text", text="B")),
        )
        replace_c = JudgeAction(action="replace", blocks=(ReplacementBlock(type="text", text="C"),))
        pass_action = JudgeAction(action="pass")

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.side_effect = [replace_ab, replace_c, pass_action]

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"x":1}', 0)), ctx)
            e1 = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(1)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"x":2}', 1)), ctx)
            e2 = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(1)), ctx)

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(2)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"x":3}', 2)), ctx)
            e3 = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(2)), ctx)

            msg = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, message_delta("tool_use")), ctx)

        all_events = e1 + e2 + e3 + msg
        starts = [e.index for e in all_events if isinstance(e, RawContentBlockStartEvent)]
        assert starts == [0, 1, 2, 3], f"1-for-1 replace bumped shift unexpectedly: expected [0,1,2,3], got {starts}"
        validate_anthropic_event_ordering(full_stream(all_events)).assert_valid()

    @pytest.mark.asyncio
    async def test_text_passthrough_after_multi_block_replacement(self):
        """Text pass after multi-replace must shift via pending_text_start rebuild.

        tool@0 → replace([A,B]); text@1 → pass — exercises the
        pending_text_start shift branch in _handle_block_stop.
        """
        policy = _make_policy()
        ctx = _make_context()

        replace_ab = JudgeAction(
            action="replace",
            blocks=(ReplacementBlock(type="text", text="A"), ReplacementBlock(type="text", text="B")),
        )
        pass_action = JudgeAction(action="pass")

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.side_effect = [replace_ab, pass_action]

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"x":1}', 0)), ctx)
            e1 = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, text_start(1)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, text_delta("hello", 1)), ctx)
            e2 = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(1)), ctx)

            msg = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, message_delta("tool_use")), ctx)

        all_events = e1 + e2 + msg
        starts = [e.index for e in all_events if isinstance(e, RawContentBlockStartEvent)]
        assert starts == [0, 1, 2], f"Text passthrough must shift to 2 after [A,B] at [0,1], got {starts}"
        validate_anthropic_event_ordering(full_stream(all_events)).assert_valid()

    @pytest.mark.asyncio
    async def test_replacement_stream_accumulates_via_anthropic_sdk(self):
        """End-to-end: SDK accumulator must rebuild the stream without IndexError.

        The validator catches structural violations; this catches what the
        actual downstream Anthropic SDK client would do with the bytes.
        """
        from anthropic.lib.streaming._messages import accumulate_event
        from anthropic.types import Message, RawMessageStartEvent, RawMessageStopEvent, Usage

        policy = _make_policy()
        ctx = _make_context()

        replace_ab = JudgeAction(
            action="replace",
            blocks=(ReplacementBlock(type="text", text="A"), ReplacementBlock(type="text", text="B")),
        )
        pass_action = JudgeAction(action="pass")

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.side_effect = [replace_ab, pass_action]

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"x":1}', 0)), ctx)
            e1 = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(1)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"y":2}', 1)), ctx)
            e2 = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(1)), ctx)

            msg = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, message_delta("tool_use")), ctx)

        message_start = RawMessageStartEvent(
            type="message_start",
            message=Message.model_construct(
                type="message",
                id="test",
                role="assistant",
                model="test-model",
                content=[],
                stop_reason=None,
                stop_sequence=None,
                usage=Usage(input_tokens=0, output_tokens=0),
            ),
        )
        message_stop = RawMessageStopEvent(type="message_stop")

        snapshot: Message | None = None
        for ev in [message_start, *e1, *e2, *msg, message_stop]:
            snapshot = accumulate_event(event=ev, current_snapshot=snapshot)
        assert snapshot is not None
        assert len(snapshot.content) == 3, f"SDK accumulator should see 3 blocks, got {len(snapshot.content)}"
