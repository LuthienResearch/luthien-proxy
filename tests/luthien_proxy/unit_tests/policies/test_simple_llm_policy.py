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
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    TextBlock,
    TextDelta,
    ToolUseBlock,
    Usage,
)

from luthien_proxy.policies.simple_llm_policy import (
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
        api_key="fake-key",
    )
    return SimpleLLMPolicy(config)


def _make_context() -> PolicyContext:
    return PolicyContext.for_testing(transaction_id="test-txn")


def _text_start(index: int = 0) -> RawContentBlockStartEvent:
    return RawContentBlockStartEvent(
        type="content_block_start",
        index=index,
        content_block=TextBlock(type="text", text=""),
    )


def _text_delta(text: str, index: int = 0) -> RawContentBlockDeltaEvent:
    return RawContentBlockDeltaEvent(
        type="content_block_delta",
        index=index,
        delta=TextDelta(type="text_delta", text=text),
    )


def _tool_start(index: int = 0, tool_id: str = "toolu_abc", name: str = "Bash") -> RawContentBlockStartEvent:
    return RawContentBlockStartEvent(
        type="content_block_start",
        index=index,
        content_block=ToolUseBlock(type="tool_use", id=tool_id, name=name, input={}),
    )


def _tool_delta(partial_json: str, index: int = 0) -> RawContentBlockDeltaEvent:
    return RawContentBlockDeltaEvent(
        type="content_block_delta",
        index=index,
        delta=InputJSONDelta(type="input_json_delta", partial_json=partial_json),
    )


def _block_stop(index: int = 0) -> RawContentBlockStopEvent:
    return RawContentBlockStopEvent(type="content_block_stop", index=index)


def _message_delta(stop_reason: str = "end_turn") -> RawMessageDeltaEvent:
    from anthropic.types.raw_message_delta_event import Delta

    return RawMessageDeltaEvent.model_construct(
        type="message_delta",
        delta=Delta.model_construct(stop_reason=stop_reason, stop_sequence=None),
        usage=Usage(input_tokens=0, output_tokens=10),
    )


def _event_types(events: list[MessageStreamEvent]) -> list[str]:
    """Extract event type strings for easy assertion."""
    return [getattr(e, "type", None) for e in events]


# ============================================================================
# Text block streaming
# ============================================================================


class TestTextBlockStreaming:
    """Test streaming event handling for text blocks."""

    @pytest.mark.asyncio
    async def test_text_pass_through(self):
        """Text block judged 'pass' emits: delta + stop."""
        policy = _make_policy()
        ctx = _make_context()

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="pass")

            # Start is passed through immediately
            start_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _text_start(0)), ctx)
            assert _event_types(start_events) == ["content_block_start"]

            # Delta is buffered
            delta_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, _text_delta("hello world", 0)), ctx
            )
            assert delta_events == []

            # Stop triggers judge, emits buffered delta + stop
            stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _block_stop(0)), ctx)
            assert _event_types(stop_events) == ["content_block_delta", "content_block_stop"]
            # Verify the buffered text is in the delta
            delta = stop_events[0]
            assert isinstance(delta, RawContentBlockDeltaEvent)
            assert isinstance(delta.delta, TextDelta)
            assert delta.delta.text == "hello world"

    @pytest.mark.asyncio
    async def test_text_blocked(self):
        """Text block judged 'block' emits just the stop (start was already emitted)."""
        policy = _make_policy()
        ctx = _make_context()

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="block")

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _text_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _text_delta("secret", 0)), ctx)

            stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _block_stop(0)), ctx)
            # Text start was already emitted, so stop must be emitted to close the block
            assert _event_types(stop_events) == ["content_block_stop"]

    @pytest.mark.asyncio
    async def test_text_replaced_with_text(self):
        """Text block judged 'replace' emits replacement start + delta + stop."""
        policy = _make_policy()
        ctx = _make_context()

        replacement = ReplacementBlock(type="text", text="[REDACTED]")
        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="replace", blocks=(replacement,))

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _text_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _text_delta("secret", 0)), ctx)

            stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _block_stop(0)), ctx)
            assert _event_types(stop_events) == [
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
            start_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_start(0)), ctx)
            assert start_events == []

            # Delta is buffered
            delta_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, _tool_delta('{"command":"echo hi"}', 0)), ctx
            )
            assert delta_events == []

            # Stop triggers judge, emits full tool block
            stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _block_stop(0)), ctx)
            assert _event_types(stop_events) == [
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
            start_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_start(0)), ctx)
            assert start_events == []

            # Delta buffered
            await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, _tool_delta('{"command":"rm -rf /"}', 0)), ctx
            )

            # Stop: blocked tool emits a text block explaining the block
            stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _block_stop(0)), ctx)
            assert _event_types(stop_events) == [
                "content_block_start",
                "content_block_delta",
                "content_block_stop",
            ], f"Blocked tool_use should emit explanatory text block, got: {_event_types(stop_events)}"
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

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, _tool_delta('{"command":"echo hi"}', 0)), ctx
            )

            stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _block_stop(0)), ctx)
            assert _event_types(stop_events) == [
                "content_block_start",
                "content_block_delta",
                "content_block_stop",
            ]
            # Verify it's a text replacement, not tool_use
            start = stop_events[0]
            assert isinstance(start, RawContentBlockStartEvent)
            assert isinstance(start.content_block, TextBlock)


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

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_delta('{"cmd":"x"}', 0)), ctx)

            stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _block_stop(0)), ctx)
            # Blocked tool emits explanatory text block
            assert _event_types(stop_events) == [
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

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_delta('{"cmd":"echo"}', 0)), ctx)

            stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _block_stop(0)), ctx)
            # Tool is passed through
            assert _event_types(stop_events) == [
                "content_block_start",
                "content_block_delta",
                "content_block_stop",
            ]

            # Warning is injected before message_delta
            msg_delta_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, _message_delta("tool_use")), ctx
            )
            types = _event_types(msg_delta_events)
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
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_delta("{}", 0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _block_stop(0)), ctx)

            # message_delta with stop_reason='tool_use' should be corrected to 'end_turn'
            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, _message_delta("tool_use")), ctx
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

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _tool_delta('{"x":1}', 0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, _block_stop(0)), ctx)

            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, _message_delta("tool_use")), ctx
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
