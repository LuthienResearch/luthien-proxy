"""Tests for the overseer stream-json parser module."""

import json
import time

import pytest
from scripts.overseer.stream_parser import (
    StreamEvent,
    TurnSummary,
    parse_stream_json,
    summarize_turn,
)
from tests.constants import DEFAULT_TEST_MODEL


def _make_line(data: dict) -> str:
    return json.dumps(data)


# ---------------------------------------------------------------------------
# Fixtures: realistic stream-json fragments
# ---------------------------------------------------------------------------

INIT_EVENT = {
    "type": "system",
    "subtype": "init",
    "session_id": "sess-abc-123",
    "model": DEFAULT_TEST_MODEL,
    "tools": ["Read", "Bash"],
}

ASSISTANT_WITH_TOOL_USE = {
    "type": "assistant",
    "message": {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Let me read that file."},
            {
                "type": "tool_use",
                "id": "tu_1",
                "name": "Read",
                "input": {"file_path": "/tmp/test.txt"},
            },
        ],
    },
    "session_id": "sess-abc-123",
}

USER_WITH_TOOL_RESULT = {
    "type": "user",
    "message": {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "tu_1",
                "content": "file contents here",
            },
        ],
    },
    "session_id": "sess-abc-123",
}

ASSISTANT_FINAL = {
    "type": "assistant",
    "message": {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "The file contains: file contents here"},
        ],
    },
    "session_id": "sess-abc-123",
}

RESULT_SUCCESS = {
    "type": "result",
    "subtype": "success",
    "session_id": "sess-abc-123",
    "result": "The file contains: file contents here",
    "is_error": False,
    "num_turns": 2,
    "total_cost_usd": 0.0042,
}

RESULT_ERROR = {
    "type": "result",
    "subtype": "error",
    "session_id": "sess-abc-123",
    "result": "Something went wrong",
    "is_error": True,
    "num_turns": 1,
    "total_cost_usd": 0.001,
}


# ---------------------------------------------------------------------------
# StreamEvent tests
# ---------------------------------------------------------------------------


class TestParseInitEvent:
    def test_parse_init_event(self):
        output = _make_line(INIT_EVENT)
        events = parse_stream_json(output)

        assert len(events) == 1
        event = events[0]
        assert event.type == "system"
        assert event.subtype == "init"
        assert event.session_id == "sess-abc-123"

    def test_session_id_from_raw(self):
        event = StreamEvent(type="system", subtype="init", raw=INIT_EVENT)
        assert event.session_id == "sess-abc-123"


class TestParseAssistantWithToolUse:
    def test_parse_assistant_with_tool_use(self):
        output = _make_line(ASSISTANT_WITH_TOOL_USE)
        events = parse_stream_json(output)

        assert len(events) == 1
        event = events[0]
        assert event.type == "assistant"

        tool_uses = event.get_tool_uses()
        assert len(tool_uses) == 1
        assert tool_uses[0]["name"] == "Read"
        assert tool_uses[0]["id"] == "tu_1"

    def test_get_text_from_assistant(self):
        event = StreamEvent(type="assistant", subtype=None, raw=ASSISTANT_WITH_TOOL_USE)
        text = event.get_text()
        assert "Let me read that file." in text

    def test_get_tool_uses_on_non_assistant_returns_empty(self):
        event = StreamEvent(type="user", subtype=None, raw=USER_WITH_TOOL_RESULT)
        assert event.get_tool_uses() == []

    def test_get_tool_results_from_user(self):
        event = StreamEvent(type="user", subtype=None, raw=USER_WITH_TOOL_RESULT)
        results = event.get_tool_results()
        assert len(results) == 1
        assert results[0]["tool_use_id"] == "tu_1"

    def test_get_tool_results_on_non_user_returns_empty(self):
        event = StreamEvent(type="assistant", subtype=None, raw=ASSISTANT_WITH_TOOL_USE)
        assert event.get_tool_results() == []


class TestParseResultEvent:
    def test_parse_result_event(self):
        output = _make_line(RESULT_SUCCESS)
        events = parse_stream_json(output)

        assert len(events) == 1
        event = events[0]
        assert event.is_result is True
        assert event.is_success is True

    def test_error_result(self):
        event = StreamEvent(type="result", subtype="error", raw=RESULT_ERROR)
        assert event.is_result is True
        assert event.is_success is False


class TestParseMalformedLineSkipped:
    def test_malformed_json_lines_are_skipped(self):
        output = "\n".join(
            [
                _make_line(INIT_EVENT),
                "this is not json",
                "{broken json: [}",
                _make_line(RESULT_SUCCESS),
            ]
        )
        events = parse_stream_json(output)
        assert len(events) == 2
        assert events[0].type == "system"
        assert events[1].type == "result"

    def test_empty_lines_are_skipped(self):
        output = "\n\n" + _make_line(INIT_EVENT) + "\n\n"
        events = parse_stream_json(output)
        assert len(events) == 1


# ---------------------------------------------------------------------------
# TurnSummary tests
# ---------------------------------------------------------------------------


def _full_turn_output() -> str:
    """Build a realistic full-turn stream-json output."""
    lines = [
        _make_line(INIT_EVENT),
        _make_line(ASSISTANT_WITH_TOOL_USE),
        _make_line(USER_WITH_TOOL_RESULT),
        _make_line(ASSISTANT_FINAL),
        _make_line(RESULT_SUCCESS),
    ]
    return "\n".join(lines)


class TestSummarizeTurn:
    def test_summarize_turn_fields(self):
        raw_output = _full_turn_output()
        now = time.time()
        summary = summarize_turn(
            raw_output=raw_output,
            turn_number=1,
            start_time=now - 10.0,
            end_time=now,
        )

        assert isinstance(summary, TurnSummary)
        assert summary.turn_number == 1
        assert summary.session_id == "sess-abc-123"
        assert summary.is_success is True
        assert summary.tools_used == ["Read"]
        assert summary.tool_call_count == 1
        assert summary.tool_result_count == 1
        assert summary.cost_usd == 0.0042
        assert summary.duration_seconds == pytest.approx(10.0, abs=0.1)
        assert "file contents here" in summary.result_text
        assert summary.anomalies == []
        assert summary.num_turns_reported == 2


class TestSummarizeTurnDetectsError:
    def test_error_result_produces_anomaly(self):
        lines = [_make_line(INIT_EVENT), _make_line(RESULT_ERROR)]
        raw_output = "\n".join(lines)
        now = time.time()

        summary = summarize_turn(
            raw_output=raw_output,
            turn_number=1,
            start_time=now - 5.0,
            end_time=now,
        )

        assert summary.is_success is False
        assert any("error" in a.lower() for a in summary.anomalies)


class TestSummarizeTurnDetectsSlowTurn:
    def test_slow_duration_produces_anomaly(self):
        raw_output = _full_turn_output()
        now = time.time()

        summary = summarize_turn(
            raw_output=raw_output,
            turn_number=1,
            start_time=now - 120.0,
            end_time=now,
            slow_threshold=60.0,
        )

        assert any("slow" in a.lower() for a in summary.anomalies)

    def test_custom_slow_threshold(self):
        raw_output = _full_turn_output()
        now = time.time()

        summary = summarize_turn(
            raw_output=raw_output,
            turn_number=1,
            start_time=now - 5.0,
            end_time=now,
            slow_threshold=3.0,
        )

        assert any("slow" in a.lower() for a in summary.anomalies)


class TestSummarizeTurnDetectsOrphanedToolCalls:
    def test_tool_calls_without_results_produce_anomaly(self):
        """Tool use with no matching tool result should be flagged."""
        lines = [
            _make_line(INIT_EVENT),
            _make_line(ASSISTANT_WITH_TOOL_USE),
            _make_line(RESULT_SUCCESS),
        ]
        raw_output = "\n".join(lines)
        now = time.time()

        summary = summarize_turn(
            raw_output=raw_output,
            turn_number=1,
            start_time=now - 5.0,
            end_time=now,
        )

        assert summary.tool_call_count == 1
        assert summary.tool_result_count == 0
        assert any("no tool result" in a.lower() for a in summary.anomalies)
