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
    TextDelta,
    ThinkingBlock,
    ToolUseBlock,
)
from tests.luthien_proxy.fixtures.anthropic_stream_validator import validate_anthropic_event_ordering
from tests.luthien_proxy.fixtures.policy_context import make_policy_context
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
    return make_policy_context(transaction_id="test-txn")


def test_init_preserves_all_config_fields():
    """__init__ must not silently drop config fields when rebuilding _config.

    Regression guard: the gateway-settings overlay rebuilds the config from the
    parsed one, overriding only model/api_base. A prior version reconstructed
    the config field-by-field and dropped max_retries/retry_delay. Pin that
    every non-overridden field round-trips.
    """
    config = SimpleLLMJudgeConfig(
        instructions="test instructions",
        on_error="block",
        temperature=0.7,
        max_tokens=1234,
        max_retries=5,
        retry_delay=2.5,
        inference_provider="user_credentials",
    )
    policy = SimpleLLMPolicy(config)

    assert policy._config.instructions == "test instructions"
    assert policy._config.on_error == "block"
    assert policy._config.temperature == 0.7
    assert policy._config.max_tokens == 1234
    assert policy._config.max_retries == 5
    assert policy._config.retry_delay == 2.5
    assert policy._config.inference_provider == "user_credentials"


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
        """Tool block judged 'pass' buffers; the start+delta+stop emit at message_delta.

        The builder buffers tool_use blocks so they always trail any text/warning
        content (Anthropic 400s on next turn if anything follows a tool_use — #708).
        """
        policy = _make_policy()
        ctx = _make_context()

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="pass")

            # Start, delta, stop all buffered — nothing emits until message_delta
            assert await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx) == []
            assert (
                await policy.on_anthropic_stream_event(
                    cast(MessageStreamEvent, tool_delta('{"command":"echo hi"}', 0)), ctx
                )
                == []
            )
            assert await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx) == []

            # message_delta flushes the buffered tool
            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )
            tool_starts = [
                e
                for e in msg_events
                if isinstance(e, RawContentBlockStartEvent) and isinstance(e.content_block, ToolUseBlock)
            ]
            assert len(tool_starts) == 1
            assert tool_starts[0].content_block.name == "Bash"

    @pytest.mark.asyncio
    async def test_tool_blocked_no_orphaned_stop(self):
        """Blocked tool: emission is deferred to message_delta — block_stop must be silent.

        The consolidated blocked-tools marker is emitted at message_delta so it
        can list every blocked / truncated tool in one text block. Verify
        block_stop emits nothing (and never a content_block_stop without a
        matching start, which would violate the Anthropic streaming protocol).
        """
        policy = _make_policy()
        ctx = _make_context()

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="block")

            start_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            assert start_events == []

            await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, tool_delta('{"command":"rm -rf /"}', 0)), ctx
            )

            stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)
            assert stop_events == [], (
                f"Blocked tool's block_stop must defer to message_delta, got: {event_types(stop_events)}"
            )

            # Marker is emitted at message_delta.
            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )
            delta = [e for e in msg_events if isinstance(e, RawContentBlockDeltaEvent)]
            assert delta, f"Expected a text delta emitted at message_delta, got: {event_types(msg_events)}"
            text_deltas = [d for d in delta if isinstance(d.delta, TextDelta)]
            assert text_deltas and "Bash" in text_deltas[0].delta.text
            assert "blocked" in text_deltas[0].delta.text

    @pytest.mark.asyncio
    async def test_tool_replaced_with_text(self):
        """Tool replacement with text: replacement event emitted at message_delta.

        Tool judges run concurrently — the decision (replace) is applied
        when the orchestrator collects results in `_handle_message_delta`.
        """
        policy = _make_policy()
        ctx = _make_context()

        replacement = ReplacementBlock(type="text", text="Tool call blocked by policy")
        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="replace", blocks=(replacement,))

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, tool_delta('{"command":"echo hi"}', 0)), ctx
            )
            assert await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx) == []

            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )

        starts = [e for e in msg_events if isinstance(e, RawContentBlockStartEvent)]
        assert [type(s.content_block).__name__ for s in starts] == ["TextBlock"]


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

            # Tool block at index 1 — blocked, marker deferred to message_delta
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(1)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta("{}", 1)), ctx)
            tool_stop = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(1)), ctx)
            assert tool_stop == [], (
                f"Blocked tool's block_stop must defer marker emission, got: {event_types(tool_stop)}"
            )

            # message_delta emits the marker AND corrects stop_reason
            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )
            marker_starts = [e for e in msg_events if isinstance(e, RawContentBlockStartEvent)]
            assert len(marker_starts) == 1, (
                f"Expected one marker block at message_delta, got: {event_types(msg_events)}"
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
        """on_error='block' + judge failure on tool_use: marker emitted at message_delta."""
        policy = _make_policy(on_error="block")
        ctx = _make_context()

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="block", judge_failed=True)

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"cmd":"x"}', 0)), ctx)

            stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)
            # Marker emission is deferred until message_delta.
            assert stop_events == [], f"block_stop must defer marker, got: {event_types(stop_events)}"

            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )
            text_deltas = [
                e for e in msg_events if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, TextDelta)
            ]
            assert text_deltas, f"Expected marker text emitted at message_delta, got: {event_types(msg_events)}"
            assert "policy evaluation unavailable" in text_deltas[0].delta.text

    @pytest.mark.asyncio
    async def test_judge_failure_pass_tool_emitted_with_warning(self):
        """on_error='pass' + judge failure on tool_use: warning emitted BEFORE the
        tool_use so the assistant message ends with the tool_use block.

        Anthropic 400s on the next turn if any content follows a tool_use within
        the assistant message ("tool_use ids were found without tool_result blocks
        immediately after"). See issue #708.
        """
        policy = _make_policy(on_error="pass")
        ctx = _make_context()

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="pass", judge_failed=True)

            # Tool buffers; nothing emits at block_stop.
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"cmd":"echo"}', 0)), ctx)
            assert await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx) == []

            # message_delta flushes: warning text block, then buffered tool, then message_delta.
            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )
            assert event_types(msg_events) == [
                "content_block_start",
                "content_block_delta",
                "content_block_stop",
                "content_block_start",
                "content_block_delta",
                "content_block_stop",
                "message_delta",
            ], event_types(msg_events)

            # Warning text in the first emitted block.
            warning_deltas = [
                e for e in msg_events[:3] if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, TextDelta)
            ]
            assert any(JUDGE_UNAVAILABLE_WARNING in d.delta.text for d in warning_deltas)

            # Last content_block_start is the tool_use — the #708 invariant.
            last_start = [e for e in msg_events if isinstance(e, RawContentBlockStartEvent)][-1]
            assert last_start.content_block.type == "tool_use"


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
        """on_error='block' + judge failure: marker at message_delta uses judge-failed phrasing."""
        policy = _make_policy(on_error="block")
        ctx = _make_context()

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="block", judge_failed=True)

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, tool_delta('{"command":"echo hi"}', 0)), ctx
            )
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)

            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )

        text_deltas = [
            e for e in msg_events if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, TextDelta)
        ]
        assert text_deltas
        assert "Bash" in text_deltas[0].delta.text
        assert "policy evaluation unavailable" in text_deltas[0].delta.text

    @pytest.mark.asyncio
    async def test_tool_blocked_intentional_uses_standard_message(self):
        """Intentional block (no judge failure): marker uses the standard blocked phrasing."""
        policy = _make_policy(on_error="block")
        ctx = _make_context()

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="block", judge_failed=False)

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, tool_delta('{"command":"rm -rf /"}', 0)), ctx
            )
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)

            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )

        text_deltas = [
            e for e in msg_events if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, TextDelta)
        ]
        assert text_deltas
        assert "blocked by policy" in text_deltas[0].delta.text
        assert "policy evaluation unavailable" not in text_deltas[0].delta.text


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


# ============================================================================
# Tool_use-trailing invariant + block-truncation (#708)
# ============================================================================
#
# Anthropic empirically rejects any non-tool_use content following the first
# tool_use in a single assistant message (live-API probe at
# tests/luthien_proxy/e2e_tests/real_anthropic/probe_tool_use_invariant.py).
# Two related behaviours are tested here:
#
# 1. Text emission after a tool_use is dropped from every emission site
#    (streaming pass / replace / block, non-streaming).
# 2. Once a tool is blocked, all subsequent tools in the same response are
#    dropped without judging — partial intervention has no clean way to
#    communicate (the "[Tool X was blocked]" marker can't follow a prior
#    tool_use either).


class TestToolUseTrailingStreaming:
    """Streaming: nothing follows the first tool_use except more tool_uses (#708)."""

    @pytest.mark.asyncio
    async def test_text_after_tool_emits_with_tool_trailing(self):
        """`[tool_pass, text_pass]` upstream → wire ends with `tool_use`.

        With concurrent tool dispatch, the text's judge runs (synchronously)
        before the tool's judge has resolved, so text commits to the wire
        immediately. The tool's buffered emission lands at message_delta.
        Either way the wire is `[..., text, tool]` — tool last, invariant ✓.
        """
        policy = _make_policy(on_error="pass")
        ctx = _make_context()

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="pass")

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"x":1}', 0)), ctx)
            tool_stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, text_start(1)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, text_delta("after tool", 1)), ctx)
            text_stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(1)), ctx)

            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )

        all_events = tool_stop_events + text_stop_events + msg_events
        starts = [e for e in all_events if isinstance(e, RawContentBlockStartEvent)]
        assert [type(s.content_block).__name__ for s in starts] == ["TextBlock", "ToolUseBlock"]

    @pytest.mark.asyncio
    async def test_text_replacement_after_tool_emits_with_tool_trailing(self):
        """Replacement text after a tool: replacement emits inline; tool flushes at finalize.

        Wire ends `[replaced, tool]` — tool last.
        """
        policy = _make_policy(on_error="pass")
        ctx = _make_context()

        # Branch by descriptor type — concurrent dispatch means a list-style
        # side_effect would be consumed in await order (text first, tool second),
        # not call order, swapping the actions.
        async def judge_by_type(descriptor, prev, ctx):
            if descriptor.type == "tool_use":
                return JudgeAction(action="pass")
            return JudgeAction(action="replace", blocks=(ReplacementBlock(type="text", text="replaced"),))

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.side_effect = judge_by_type

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"x":1}', 0)), ctx)
            tool_stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, text_start(1)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, text_delta("orig", 1)), ctx)
            text_stop_events = await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(1)), ctx)

            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )

        all_events = tool_stop_events + text_stop_events + msg_events
        starts = [e for e in all_events if isinstance(e, RawContentBlockStartEvent)]
        assert [type(s.content_block).__name__ for s in starts] == ["TextBlock", "ToolUseBlock"]
        text_deltas = [
            e for e in all_events if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, TextDelta)
        ]
        assert any("replaced" in d.delta.text for d in text_deltas)

    @pytest.mark.asyncio
    async def test_judge_failure_after_buffered_tool_emits_warning_before_tool(self):
        """Judge fails on text emitted AFTER a tool was buffered → warning still lands.

        With the builder buffering tool_use until message_delta, a late judge
        failure on a post-tool text block can still surface the warning *before*
        the tool flush, satisfying the #708 invariant. Wire order ends up
        [text, warning, tool_use] — tool_use last, warning visible to the user.
        """
        policy = _make_policy(on_error="pass")
        ctx = _make_context()

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.side_effect = [
                JudgeAction(action="pass"),  # tool_use passes
                JudgeAction(action="pass", judge_failed=True),  # later text fails judge
            ]

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta('{"x":1}', 0)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(0)), ctx)

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, text_start(1)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, text_delta("after", 1)), ctx)
            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(1)), ctx)

            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )

        # Final content_block_start must be the tool_use (#708).
        starts = [e for e in msg_events if isinstance(e, RawContentBlockStartEvent)]
        assert starts[-1].content_block.type == "tool_use"

        # Warning text appears somewhere before the tool_use start.
        text_deltas = [
            e for e in msg_events if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, TextDelta)
        ]
        assert any(JUDGE_UNAVAILABLE_WARNING in d.delta.text for d in text_deltas)

    @pytest.mark.asyncio
    async def test_text_replaced_with_tool_then_passthrough_text(self):
        """Regression: text→tool_use replacement followed by a passing text.

        The replaced tool buffers until finalize; the subsequent pass-text
        emits live at its block_stop. Total wire ends `[text, tool]` —
        tool_use trails the live text, satisfying #708 by caller discipline.
        """
        policy = _make_policy(on_error="pass")
        ctx = _make_context()
        all_events: list[MessageStreamEvent] = []

        async def feed(event: MessageStreamEvent) -> None:
            all_events.extend(await policy.on_anthropic_stream_event(event, ctx))

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.side_effect = [
                JudgeAction(
                    action="replace",
                    blocks=(ReplacementBlock(type="tool_use", name="Bash", input={"cmd": "ls"}),),
                ),
                JudgeAction(action="pass"),
            ]

            await feed(cast(MessageStreamEvent, text_start(0)))
            await feed(cast(MessageStreamEvent, text_delta("orig", 0)))
            await feed(cast(MessageStreamEvent, block_stop(0)))

            await feed(cast(MessageStreamEvent, text_start(1)))
            await feed(cast(MessageStreamEvent, text_delta("after", 1)))
            await feed(cast(MessageStreamEvent, block_stop(1)))

            await feed(cast(MessageStreamEvent, message_delta("tool_use")))

        starts = [e for e in all_events if isinstance(e, RawContentBlockStartEvent)]
        assert [type(s.content_block).__name__ for s in starts] == ["TextBlock", "ToolUseBlock"]


class TestConcurrentToolJudgingWithBail:
    """Tool judges dispatched concurrently at block_stop; collected at message_delta.

    The first `block` decision (in submission order) cancels every still-pending
    tool judge. Bailed tools surface in the consolidated blocked-tools marker
    so the next turn can see what was attempted.
    """

    @pytest.mark.asyncio
    async def test_block_bails_subsequent_tools(self):
        """`[A_pass, B_block, C_slow_pass]` upstream → wire: `[marker(B,C), A]`.

        B's block fires before C completes. C is still pending when bail
        triggers, so it gets cancelled and surfaces as Bailed. The marker
        names both blocked B and bailed C.
        """
        import asyncio

        policy = _make_policy(on_error="block")
        ctx = _make_context()
        call_count = 0

        async def judge_side_effect(_descriptor, _prev, _ctx):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return JudgeAction(action="pass")
            if call_count == 2:
                return JudgeAction(action="block")
            await asyncio.sleep(10)  # cancelled by bail
            return JudgeAction(action="pass")

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.side_effect = judge_side_effect

            for idx in (0, 1, 2):
                await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(idx)), ctx)
                await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta("{}", idx)), ctx)
                assert await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(idx)), ctx) == []

            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )

        starts = [e for e in msg_events if isinstance(e, RawContentBlockStartEvent)]
        assert [type(s.content_block).__name__ for s in starts] == ["TextBlock", "ToolUseBlock"]

        text_deltas = [
            e for e in msg_events if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, TextDelta)
        ]
        marker = text_deltas[0].delta.text
        assert "Tool calls" in marker
        assert marker.count("Bash") == 2

    @pytest.mark.asyncio
    async def test_first_block_bails_everything_after(self):
        """`[A_block, B_slow_block, C_slow_pass]` upstream → wire: `[marker]` only.

        A blocks immediately; B and C are pending when bail fires, both
        cancelled. No tool_use survives.
        """
        import asyncio

        policy = _make_policy(on_error="block")
        ctx = _make_context()
        call_count = 0

        async def judge_side_effect(_descriptor, _prev, _ctx):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return JudgeAction(action="block")
            await asyncio.sleep(10)  # cancelled by bail
            return JudgeAction(action="pass" if call_count == 3 else "block")

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.side_effect = judge_side_effect

            for idx in (0, 1, 2):
                await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(idx)), ctx)
                await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta("{}", idx)), ctx)
                await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(idx)), ctx)

            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )

        starts = [e for e in msg_events if isinstance(e, RawContentBlockStartEvent)]
        assert [type(s.content_block).__name__ for s in starts] == ["TextBlock"]

    @pytest.mark.asyncio
    async def test_concurrent_dispatch_skips_judges_after_bail(self):
        """When a block fires early, subsequent tools' judges are cancelled.

        Without cancellation, all N judges would always complete. With it, we
        save real latency once a block decision arrives.
        """
        import asyncio

        policy = _make_policy(on_error="block")
        ctx = _make_context()

        completed = 0
        call_count = 0

        async def judge_side_effect(descriptor, prev, ctx_):
            nonlocal completed, call_count
            call_count += 1
            if call_count == 1:
                # First tool blocks. Returns immediately so bail fires before
                # the slow judges below can complete.
                completed += 1
                return JudgeAction(action="block")
            # Subsequent tools: take a long time. Cancellation must abort.
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                raise
            completed += 1
            return JudgeAction(action="pass")

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.side_effect = judge_side_effect

            for idx in (0, 1, 2):
                await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_start(idx)), ctx)
                await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta("{}", idx)), ctx)
                await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(idx)), ctx)

            await policy.on_anthropic_stream_event(cast(MessageStreamEvent, message_delta("tool_use")), ctx)

        assert completed == 1, f"Only the blocking judge should complete; got {completed}"


class TestToolUseTrailingNonStreaming:
    """Non-streaming: assistant content list must satisfy the invariant after processing."""

    @pytest.mark.asyncio
    async def test_text_after_tool_use_reordered_before_tool(self):
        """`[tool, text]` upstream → `[text, tool]` downstream. Text is preserved, just reordered."""
        policy = _make_policy(on_error="pass")
        ctx = _make_context()

        response: dict[str, Any] = {
            "content": [
                {"type": "tool_use", "id": "toolu_a", "name": "Bash", "input": {"x": 1}},
                {"type": "text", "text": "trailing"},
            ],
            "stop_reason": "tool_use",
        }

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="pass")
            result = await policy.on_anthropic_response(response, ctx)

        types = [b.get("type") for b in result["content"]]
        assert types == ["text", "tool_use"], f"Text must be reordered before tool, got: {types}"

    @pytest.mark.asyncio
    async def test_text_between_tools_reordered_before_tools(self):
        """`[tool_A, text, tool_B]` → `[text, tool_A, tool_B]`. Text preserved, tools trail."""
        policy = _make_policy(on_error="pass")
        ctx = _make_context()

        response: dict[str, Any] = {
            "content": [
                {"type": "tool_use", "id": "toolu_a", "name": "Bash", "input": {"a": 1}},
                {"type": "text", "text": "between"},
                {"type": "tool_use", "id": "toolu_b", "name": "Bash", "input": {"b": 2}},
            ],
            "stop_reason": "tool_use",
        }

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="pass")
            result = await policy.on_anthropic_response(response, ctx)

        types = [b.get("type") for b in result["content"]]
        assert types == ["text", "tool_use", "tool_use"], f"Text must move before tools, got: {types}"

    @pytest.mark.asyncio
    async def test_marker_lists_all_blocked_tool_names(self):
        """Multiple distinct tools blocked: the consolidated marker lists every name.

        Each tool is judged independently; blocked ones accumulate into a single
        marker text block emitted in the pre-tool slot.
        """
        policy = _make_policy(on_error="block")
        ctx = _make_context()

        def _tool_start_named(idx: int, name: str) -> Any:
            from anthropic.types import ToolUseBlock as _TUB

            return cast(
                MessageStreamEvent,
                RawContentBlockStartEvent(
                    type="content_block_start",
                    index=idx,
                    content_block=_TUB(type="tool_use", id=f"toolu_{idx}", name=name, input={}),
                ),
            )

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeAction(action="block")

            for idx, name in [(0, "Bash"), (1, "Read"), (2, "Edit")]:
                await policy.on_anthropic_stream_event(_tool_start_named(idx, name), ctx)
                await policy.on_anthropic_stream_event(cast(MessageStreamEvent, tool_delta("{}", idx)), ctx)
                await policy.on_anthropic_stream_event(cast(MessageStreamEvent, block_stop(idx)), ctx)

            msg_events = await policy.on_anthropic_stream_event(
                cast(MessageStreamEvent, message_delta("tool_use")), ctx
            )

        text_deltas = [
            e for e in msg_events if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, TextDelta)
        ]
        assert text_deltas, f"Expected a marker block, got: {event_types(msg_events)}"
        marker_text = text_deltas[0].delta.text
        for name in ("Bash", "Read", "Edit"):
            assert name in marker_text
        assert "Tool calls" in marker_text

    @pytest.mark.asyncio
    async def test_block_in_middle_judges_all_tools_non_streaming(self):
        """`[A_pass, B_block, C_pass]` → `[marker(B), A, C]`. Each tool judged independently."""
        policy = _make_policy(on_error="block")
        ctx = _make_context()

        response: dict[str, Any] = {
            "content": [
                {"type": "tool_use", "id": "toolu_a", "name": "Bash", "input": {"a": 1}},
                {"type": "tool_use", "id": "toolu_b", "name": "Bash", "input": {"b": 2}},
                {"type": "tool_use", "id": "toolu_c", "name": "Bash", "input": {"c": 3}},
            ],
            "stop_reason": "tool_use",
        }

        with patch.object(policy, "_judge_block", new_callable=AsyncMock) as mock_judge:
            mock_judge.side_effect = [
                JudgeAction(action="pass"),
                JudgeAction(action="block"),
                JudgeAction(action="pass"),
            ]
            result = await policy.on_anthropic_response(response, ctx)

        types = [b.get("type") for b in result["content"]]
        assert types == ["text", "tool_use", "tool_use"], f"Got: {types}"
        assert mock_judge.call_count == 3
