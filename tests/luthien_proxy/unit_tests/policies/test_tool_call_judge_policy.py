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

from luthien_proxy.policies.tool_call_judge_policy import (
    ToolCallJudgeConfig,
    ToolCallJudgePolicy,
)
from luthien_proxy.policies.tool_call_judge_utils import JudgeResult
from luthien_proxy.policy_core.anthropic_message_builder import AnthropicMessageBuilder
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
        """Allowed tool reconstructed at message_delta: start + json_delta + stop, then message_delta."""
        policy = _make_policy()
        ctx = _make_context()

        with patch.object(policy, "_evaluate_and_maybe_block", new_callable=AsyncMock) as mock_eval:
            mock_eval.return_value = None  # allowed

            assert (
                await policy.on_anthropic_stream_event(
                    cast(MessageStreamEvent, tool_start(0, tool_id="toolu_123", name="Bash")), ctx
                )
                == []
            )
            assert (
                await policy.on_anthropic_stream_event(
                    cast(MessageStreamEvent, tool_delta('{"command":"echo hello"}', 0)), ctx
                )
                == []
            )
            assert await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx) == []

            # Tool emitted at message_delta.
            emitted = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, message_delta("tool_use")), ctx)

            assert event_types(emitted) == [
                "content_block_start",
                "content_block_delta",
                "content_block_stop",
                "message_delta",
            ]
            start_event = emitted[0]
            assert isinstance(start_event, RawContentBlockStartEvent)
            assert isinstance(start_event.content_block, ToolUseBlock)
            assert start_event.content_block.id == "toolu_123"
            assert start_event.content_block.name == "Bash"
            delta_event = emitted[1]
            assert isinstance(delta_event, RawContentBlockDeltaEvent)
            assert isinstance(delta_event.delta, InputJSONDelta)
            # Builder preserves the original streamed bytes verbatim.
            assert delta_event.delta.partial_json == '{"command":"echo hello"}'


# ============================================================================
# Streaming Tool Blocked
# ============================================================================


class TestStreamingToolBlocked:
    """Test streaming event handling for blocked tool_use blocks."""

    @pytest.mark.asyncio
    async def test_tool_blocked_emits_text_replacement(self):
        """Blocked tool emits text start + text_delta + stop in the pre-tool slot.

        With the trailing-tool_use invariant (#708), the blocked-text replacement
        emits live at block_stop (before any tool reaches the wire); the
        message_delta only carries the corrected stop_reason and any deferred
        flush events.
        """
        policy = _make_policy()
        ctx = _make_context()

        judge_result = JudgeResult(probability=0.9, explanation="dangerous operation", prompt=[], response_text="")

        with patch.object(policy, "_evaluate_and_maybe_block", new_callable=AsyncMock) as mock_eval:
            mock_eval.return_value = judge_result

            all_events: list[MessageStreamEvent] = []
            all_events += await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, tool_start(0, name="Bash")), ctx
            )
            all_events += await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, tool_delta('{"command":"rm -rf /"}', 0)), ctx
            )
            all_events += await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)
            all_events += await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )

            assert event_types(all_events[:3]) == [
                "content_block_start",
                "content_block_delta",
                "content_block_stop",
            ]
            start_event = all_events[0]
            assert isinstance(start_event, RawContentBlockStartEvent)
            assert isinstance(start_event.content_block, TextBlock)
            delta_event = all_events[1]
            assert isinstance(delta_event, RawContentBlockDeltaEvent)
            assert isinstance(delta_event.delta, TextDelta)
            assert "Bash" in delta_event.delta.text
            assert "dangerous operation" in delta_event.delta.text

    @pytest.mark.asyncio
    async def test_blocked_tool_produces_text_not_tool_use(self):
        """Blocking is observable in the final emitted content: text block, no tool_use."""
        policy = _make_policy()
        ctx = _make_context()

        judge_result = JudgeResult(probability=0.9, explanation="blocked", prompt=[], response_text="")

        with patch.object(policy, "_evaluate_and_maybe_block", new_callable=AsyncMock) as mock_eval:
            mock_eval.return_value = judge_result

            all_events: list[MessageStreamEvent] = []
            all_events += await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            all_events += await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, tool_delta('{"x":1}', 0)), ctx
            )
            all_events += await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)
            all_events += await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )

            start_event = all_events[0]
            assert isinstance(start_event, RawContentBlockStartEvent)
            assert isinstance(start_event.content_block, TextBlock)
            # No ToolUseBlock anywhere in the wire output.
            tool_starts = [
                e
                for e in all_events
                if isinstance(e, RawContentBlockStartEvent) and isinstance(e.content_block, ToolUseBlock)
            ]
            assert tool_starts == []


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
        """When _evaluate_and_maybe_block returns high-probability result, tool is blocked."""
        policy = _make_policy()
        ctx = _make_context()

        # Simulate judge failure: internal error handling returns high-prob result
        judge_result = JudgeResult(
            probability=1.0,
            explanation="Judge evaluation failed: connection timeout",
            prompt=[],
            response_text="",
        )

        with patch.object(policy, "_evaluate_and_maybe_block", new_callable=AsyncMock) as mock_eval:
            mock_eval.return_value = judge_result

            all_events: list[MessageStreamEvent] = []
            all_events += await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            all_events += await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, tool_delta('{"cmd":"dangerous"}', 0)), ctx
            )
            all_events += await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)
            all_events += await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )

            assert event_types(all_events[:3]) == [
                "content_block_start",
                "content_block_delta",
                "content_block_stop",
            ]
            delta = all_events[1]
            assert isinstance(delta, RawContentBlockDeltaEvent)
            assert "evaluation failed" in delta.delta.text.lower()


# ============================================================================
# Streaming Multiple Tools
# ============================================================================


class TestStreamingMultipleTools:
    """Test mixed tool blocks, some allowed and some blocked."""

    @pytest.mark.asyncio
    async def test_mixed_tools_one_allowed_one_blocked(self):
        """Two tool blocks at different indices: one allowed, one blocked.

        Wire shape: blocked-text emits live at the blocked tool's
        block_stop; the surviving allowed tool emits at finalize. Total
        wire is `[blocked_text, allowed_tool]` — tool_use trails (#708).
        """
        policy = _make_policy()
        ctx = _make_context()

        judge_result_blocked = JudgeResult(probability=0.8, explanation="harmful", prompt=[], response_text="")

        all_events: list[MessageStreamEvent] = []

        async def feed(event: MessageStreamEvent) -> None:
            all_events.extend(await policy.on_anthropic_stream_event(event, ctx))

        with patch.object(policy, "_evaluate_and_maybe_block", new_callable=AsyncMock) as mock_eval:
            mock_eval.side_effect = [None, judge_result_blocked]

            await feed(cast(MessageStreamEvent, tool_start(0, tool_id="toolu_1", name="Tool1")))
            await feed(cast(MessageStreamEvent, tool_delta('{"param":"value"}', 0)))
            await feed(cast(MessageStreamEvent, block_stop(0)))

            await feed(cast(MessageStreamEvent, tool_start(1, tool_id="toolu_2", name="Tool2")))
            await feed(cast(MessageStreamEvent, tool_delta('{"param":"dangerous"}', 1)))
            await feed(cast(MessageStreamEvent, block_stop(1)))

            await feed(cast(MessageStreamEvent, message_delta("tool_use")))

        starts = [e for e in all_events if isinstance(e, RawContentBlockStartEvent)]
        block_types = [type(s.content_block).__name__ for s in starts]
        assert block_types == ["TextBlock", "ToolUseBlock"]

        text_delta_event = next(
            e for e in all_events if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, TextDelta)
        )
        assert "Tool2" in text_delta_event.delta.text
        tool_start_event = next(s for s in starts if isinstance(s.content_block, ToolUseBlock))
        assert tool_start_event.content_block.name == "Tool1"


# ============================================================================
# Streaming stop_reason correction
# ============================================================================


class TestStreamingStopReasonCorrection:
    """Test that the streaming path rewrites stop_reason when all tool_use blocked.

    Mirrors the non-streaming behavior at tool_call_judge_policy.py:250-252.
    Without this rewrite, a downstream consumer (e.g. Claude Code) sees
    stop_reason='tool_use' but no tool_use content block — and gives up.
    """

    @pytest.mark.asyncio
    async def test_stop_reason_corrected_after_tool_blocked(self):
        """Single blocked tool_use → message_delta('tool_use') rewritten to 'end_turn'."""
        policy = _make_policy()
        ctx = _make_context()

        judge_result = JudgeResult(probability=0.9, explanation="harmful", prompt=[], response_text="")

        with patch.object(policy, "_evaluate_and_maybe_block", new_callable=AsyncMock) as mock_eval:
            mock_eval.return_value = judge_result

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"x":1}', 0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)

            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )

        delta_events = [e for e in msg_events if isinstance(e, RawMessageDeltaEvent)]
        assert len(delta_events) == 1
        assert delta_events[0].delta.stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_stop_reason_kept_when_tool_passed(self):
        """Single allowed tool_use → message_delta stop_reason stays 'tool_use'."""
        policy = _make_policy()
        ctx = _make_context()

        with patch.object(policy, "_evaluate_and_maybe_block", new_callable=AsyncMock) as mock_eval:
            mock_eval.return_value = None  # allowed

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"x":1}', 0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)

            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )

        delta_events = [e for e in msg_events if isinstance(e, RawMessageDeltaEvent)]
        assert len(delta_events) == 1
        assert delta_events[0].delta.stop_reason == "tool_use"

    @pytest.mark.asyncio
    async def test_stop_reason_kept_with_mixed_tools(self):
        """One allowed and one blocked → stop_reason stays 'tool_use'."""
        policy = _make_policy()
        ctx = _make_context()

        with patch.object(policy, "_evaluate_and_maybe_block", new_callable=AsyncMock) as mock_eval:
            mock_eval.side_effect = [
                None,  # index 0 allowed
                JudgeResult(probability=0.9, explanation="harmful", prompt=[], response_text=""),  # index 1 blocked
            ]

            await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, tool_start(0, tool_id="toolu_1", name="Tool1")), ctx
            )
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"x":1}', 0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)

            await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, tool_start(1, tool_id="toolu_2", name="Tool2")), ctx
            )
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"x":2}', 1)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(1)), ctx)

            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )

        delta_events = [e for e in msg_events if isinstance(e, RawMessageDeltaEvent)]
        assert len(delta_events) == 1
        assert delta_events[0].delta.stop_reason == "tool_use"

    @pytest.mark.asyncio
    async def test_stop_reason_unchanged_with_no_tools(self):
        """No tool_use seen → message_delta('end_turn') passes through unchanged."""
        policy = _make_policy()
        ctx = _make_context()

        original = message_delta("end_turn")
        msg_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, original), ctx)

        assert msg_events == [original]

    @pytest.mark.asyncio
    async def test_stop_reason_rewritten_to_match_emitted_content(self):
        """When all tools are blocked, stop_reason is rewritten to end_turn regardless of upstream value.

        The builder owns stop_reason consistency: the value on the wire
        must match what content actually shipped. Preserving an upstream
        max_tokens while emitting no tool_use would mislead downstream.
        """
        policy = _make_policy()
        ctx = _make_context()

        judge_result = JudgeResult(probability=0.9, explanation="harmful", prompt=[], response_text="")

        with patch.object(policy, "_evaluate_and_maybe_block", new_callable=AsyncMock) as mock_eval:
            mock_eval.return_value = judge_result

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"x":1}', 0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)

            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("max_tokens")), ctx
            )

        delta_events = [e for e in msg_events if isinstance(e, RawMessageDeltaEvent)]
        assert len(delta_events) == 1
        assert delta_events[0].delta.stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_stop_reason_rewritten_to_end_turn_when_no_blocks_seen(self):
        """Upstream sent stop_reason='tool_use' but no tool_use blocks were seen.

        The builder rewrites stop_reason to match what actually shipped on
        the wire. A malformed upstream (no tool_use content but stop_reason
        tool_use) is corrected, not silently passed through.
        """
        policy = _make_policy()
        ctx = _make_context()

        msg_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, message_delta("tool_use")), ctx)

        delta_events = [e for e in msg_events if isinstance(e, RawMessageDeltaEvent)]
        assert len(delta_events) == 1
        assert delta_events[0].delta.stop_reason == "end_turn"


# ============================================================================
# Non-Streaming Response
# ============================================================================


class TestNonStreamingResponse:
    """Test on_anthropic_response (non-streaming path)."""

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

        with patch.object(policy, "_evaluate_and_maybe_block", new_callable=AsyncMock) as mock_eval:
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

        with patch.object(policy, "_evaluate_and_maybe_block", new_callable=AsyncMock) as mock_eval:
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

        with patch.object(policy, "_evaluate_and_maybe_block", new_callable=AsyncMock) as mock_eval:
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
        with patch.object(policy, "_evaluate_and_maybe_block", new_callable=AsyncMock) as mock_eval:
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

        with patch.object(policy, "_evaluate_and_maybe_block", new_callable=AsyncMock) as mock_eval:
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
        """on_anthropic_streaming_policy_complete must remove a populated buffer."""
        policy = _make_policy()
        ctx = _make_context()

        # Drive a tool_use start to populate request state with a buffer.
        await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)

        # Cleanup must remove the populated buffer (not just no-op on empty state).
        await policy.on_anthropic_streaming_policy_complete(ctx)
        assert ctx.pop_request_state(policy, AnthropicMessageBuilder) is None


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

        message = policy._format_blocked_message(tool_call, judge_result)

        assert "Bash" in message
        assert "rm -rf /" in message
        assert "0.95" in message
        assert "destructive operation" in message

    def test_custom_template(self):
        """policy with custom blocked_message_template uses it."""
        policy = _make_policy(blocked_message_template="BLOCKED: {tool_name} with probability {probability:.1f}")

        tool_call = {"name": "Python", "arguments": "{}"}
        judge_result = JudgeResult(probability=0.75, explanation="explanation", prompt=[], response_text="")

        message = policy._format_blocked_message(tool_call, judge_result)

        assert message == "BLOCKED: Python with probability 0.8"

    def test_long_arguments_truncated(self):
        """arguments longer than TOOL_ARGS_TRUNCATION_LENGTH are truncated."""
        from luthien_proxy.utils.constants import TOOL_ARGS_TRUNCATION_LENGTH

        policy = _make_policy()

        long_args = '{"data":"' + "x" * 10000 + '"}'
        tool_call = {"name": "Tool", "arguments": long_args}
        judge_result = JudgeResult(probability=0.5, explanation="test", prompt=[], response_text="")

        message = policy._format_blocked_message(tool_call, judge_result)

        # Truncated prefix appears in message, but full args do not
        assert long_args[:TOOL_ARGS_TRUNCATION_LENGTH] in message
        assert long_args not in message

    def test_template_with_empty_explanation(self):
        """format handles empty explanation via 'or' fallback."""
        policy = _make_policy()

        tool_call = {"name": "Tool", "arguments": "{}"}
        judge_result = JudgeResult(probability=0.9, explanation="", prompt=[], response_text="")

        message = policy._format_blocked_message(tool_call, judge_result)

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
