"""Tests for the Anthropic streaming protocol compliance validator."""

from __future__ import annotations

import pytest
from tests.luthien_proxy.fixtures.anthropic_stream_validator import (
    StreamValidationResult,
    validate_anthropic_event_ordering,
)

# -- Helpers to build minimal event dicts --------------------------------------


def _msg_start() -> dict:
    return {"type": "message_start", "message": {"id": "msg_test"}}


def _block_start(index: int) -> dict:
    return {"type": "content_block_start", "index": index}


def _block_delta(index: int) -> dict:
    return {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": "hi"}}


def _block_stop(index: int) -> dict:
    return {"type": "content_block_stop", "index": index}


def _msg_delta() -> dict:
    return {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}


def _msg_stop() -> dict:
    return {"type": "message_stop"}


def _text_block(index: int) -> list[dict]:
    """Complete content block lifecycle for a text block."""
    return [_block_start(index), _block_delta(index), _block_stop(index)]


def _valid_single_block_stream() -> list[dict]:
    return [_msg_start(), *_text_block(0), _msg_delta(), _msg_stop()]


def _valid_parallel_tool_stream(n: int = 3) -> list[dict]:
    """Valid stream with n parallel tool_use blocks."""
    events: list[dict] = [_msg_start()]
    for i in range(n):
        events.extend(_text_block(i))
    events.extend([_msg_delta(), _msg_stop()])
    return events


# -- Valid streams should pass -------------------------------------------------


class TestValidStreams:
    def test_single_text_block(self):
        result = validate_anthropic_event_ordering(_valid_single_block_stream())
        result.assert_valid()

    def test_parallel_tool_blocks(self):
        result = validate_anthropic_event_ordering(_valid_parallel_tool_stream(3))
        result.assert_valid()

    def test_multiple_deltas_per_block(self):
        events = [
            _msg_start(),
            _block_start(0),
            _block_delta(0),
            _block_delta(0),
            _block_delta(0),
            _block_stop(0),
            _msg_delta(),
            _msg_stop(),
        ]
        result = validate_anthropic_event_ordering(events)
        result.assert_valid()

    def test_ping_events_are_tolerated(self):
        events = [
            _msg_start(),
            {"type": "ping"},
            *_text_block(0),
            _msg_delta(),
            _msg_stop(),
        ]
        result = validate_anthropic_event_ordering(events)
        result.assert_valid()


# -- Rule: message_start must be first ----------------------------------------


class TestMessageStartFirst:
    def test_missing_message_start(self):
        events = [*_text_block(0), _msg_delta(), _msg_stop()]
        result = validate_anthropic_event_ordering(events)
        assert not result.valid
        assert any(v.rule == "message_start_first" for v in result.violations)

    def test_message_start_not_first(self):
        events = [{"type": "ping"}, _msg_start(), *_text_block(0), _msg_delta(), _msg_stop()]
        result = validate_anthropic_event_ordering(events)
        assert any(v.rule == "message_start_first" for v in result.violations)


# -- Rule: message_stop must be last -------------------------------------------


class TestMessageStopLast:
    def test_missing_message_stop(self):
        events = [_msg_start(), *_text_block(0), _msg_delta()]
        result = validate_anthropic_event_ordering(events)
        assert not result.valid
        assert any(v.rule == "message_stop_last" for v in result.violations)


# -- Rule: content blocks before message_delta ---------------------------------


class TestContentBeforeMessageDelta:
    def test_content_block_after_message_delta(self):
        """The exact bug from PR #356: content blocks injected after message_delta."""
        events = [
            _msg_start(),
            *_text_block(0),
            _msg_delta(),
            # These blocks come AFTER message_delta — violation!
            *_text_block(1),
            _msg_stop(),
        ]
        result = validate_anthropic_event_ordering(events)
        assert not result.valid
        violations = [v for v in result.violations if v.rule == "content_before_message_delta"]
        assert len(violations) == 3  # start + delta + stop for block 1

    def test_warning_injection_after_message_delta(self):
        """Simulates the PR #356 bug: warning text block after message_delta."""
        events = [
            _msg_start(),
            *_text_block(0),
            *_text_block(1),
            _msg_delta(),
            # Warning block injected at message_stop — wrong!
            _block_start(2),
            _block_delta(2),
            _block_stop(2),
            _msg_stop(),
        ]
        result = validate_anthropic_event_ordering(events)
        assert not result.valid
        assert any(v.rule == "content_before_message_delta" for v in result.violations)


# -- Rule: block lifecycle (start → delta → stop) -----------------------------


class TestBlockLifecycle:
    def test_delta_without_start(self):
        events = [_msg_start(), _block_delta(0), _block_stop(0), _msg_delta(), _msg_stop()]
        result = validate_anthropic_event_ordering(events)
        assert any(v.rule == "delta_after_start" for v in result.violations)

    def test_stop_without_start(self):
        events = [_msg_start(), _block_stop(0), _msg_delta(), _msg_stop()]
        result = validate_anthropic_event_ordering(events)
        assert any(v.rule == "stop_after_start" for v in result.violations)

    def test_delta_after_stop(self):
        events = [
            _msg_start(),
            _block_start(0),
            _block_delta(0),
            _block_stop(0),
            _block_delta(0),  # delta after stop
            _msg_delta(),
            _msg_stop(),
        ]
        result = validate_anthropic_event_ordering(events)
        assert any(v.rule == "delta_before_stop" for v in result.violations)

    def test_double_stop(self):
        events = [
            _msg_start(),
            _block_start(0),
            _block_stop(0),
            _block_stop(0),
            _msg_delta(),
            _msg_stop(),
        ]
        result = validate_anthropic_event_ordering(events)
        assert any(v.rule == "block_stopped_once" for v in result.violations)

    def test_unclosed_block(self):
        events = [_msg_start(), _block_start(0), _block_delta(0), _msg_delta(), _msg_stop()]
        result = validate_anthropic_event_ordering(events)
        assert any(v.rule == "blocks_closed" for v in result.violations)


# -- Rule: block start indices monotonically increasing ------------------------


class TestBlockIndexOrdering:
    def test_duplicate_start_index(self):
        events = [
            _msg_start(),
            _block_start(0),
            _block_stop(0),
            _block_start(0),  # duplicate
            _block_stop(0),
            _msg_delta(),
            _msg_stop(),
        ]
        result = validate_anthropic_event_ordering(events)
        assert any(v.rule == "block_start_monotonic" for v in result.violations)

    def test_decreasing_start_index(self):
        events = [
            _msg_start(),
            *_text_block(1),
            *_text_block(0),  # decreasing
            _msg_delta(),
            _msg_stop(),
        ]
        result = validate_anthropic_event_ordering(events)
        assert any(v.rule == "block_start_monotonic" for v in result.violations)


# -- Edge cases ----------------------------------------------------------------


class TestEdgeCases:
    def test_empty_stream(self):
        result = validate_anthropic_event_ordering([])
        assert not result.valid
        assert any(v.rule == "non_empty" for v in result.violations)

    def test_assert_valid_raises_on_violations(self):
        result = validate_anthropic_event_ordering([])
        with pytest.raises(AssertionError, match="Anthropic streaming protocol violations"):
            result.assert_valid()

    def test_assert_valid_passes_on_valid_stream(self):
        result = validate_anthropic_event_ordering(_valid_single_block_stream())
        result.assert_valid()  # should not raise

    def test_valid_result_properties(self):
        result = StreamValidationResult()
        assert result.valid is True
        assert result.violations == []


# -- Works with Pydantic model objects (unit test style) -----------------------


class TestPydanticObjects:
    def test_valid_stream_with_sdk_types(self):
        """Validator works with anthropic SDK Pydantic types, not just dicts."""
        from typing import cast

        from anthropic.lib.streaming import MessageStreamEvent
        from anthropic.types import (
            MessageDeltaUsage,
            RawContentBlockDeltaEvent,
            RawContentBlockStartEvent,
            RawContentBlockStopEvent,
            RawMessageDeltaEvent,
            RawMessageStartEvent,
            RawMessageStopEvent,
            TextBlock,
            TextDelta,
        )

        events: list[MessageStreamEvent] = [
            cast(
                MessageStreamEvent,
                RawMessageStartEvent(
                    type="message_start",
                    message={  # type: ignore[arg-type]
                        "id": "msg_test",
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                        "model": "claude-haiku-4-5-20251001",
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 10, "output_tokens": 0},
                    },
                ),
            ),
            cast(
                MessageStreamEvent,
                RawContentBlockStartEvent(
                    type="content_block_start",
                    index=0,
                    content_block=TextBlock(type="text", text=""),
                ),
            ),
            cast(
                MessageStreamEvent,
                RawContentBlockDeltaEvent(
                    type="content_block_delta",
                    index=0,
                    delta=TextDelta(type="text_delta", text="Hello"),
                ),
            ),
            cast(
                MessageStreamEvent,
                RawContentBlockStopEvent(type="content_block_stop", index=0),
            ),
            cast(
                MessageStreamEvent,
                RawMessageDeltaEvent(
                    type="message_delta",
                    delta={"stop_reason": "end_turn", "stop_sequence": None},  # type: ignore[arg-type]
                    usage=MessageDeltaUsage(output_tokens=5),
                ),
            ),
            cast(
                MessageStreamEvent,
                RawMessageStopEvent(type="message_stop"),
            ),
        ]

        result = validate_anthropic_event_ordering(events)
        result.assert_valid()
