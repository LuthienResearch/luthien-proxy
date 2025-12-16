# ABOUTME: Unit tests for V2 debug service layer
# ABOUTME: Tests business logic functions for fetching events and computing diffs

"""Tests for V2 debug service layer.

These tests focus on the pure business logic functions without
FastAPI dependencies. This makes tests faster and easier to write.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from luthien_proxy.debug.service import (
    build_tempo_url,
    compute_request_diff,
    compute_response_diff,
    extract_message_content,
    fetch_call_diff,
    fetch_call_events,
    fetch_recent_calls,
)


class TestBuildTempoUrl:
    """Test Tempo URL building."""

    def test_default_grafana_url(self):
        """Test URL building with default Grafana URL."""
        url = build_tempo_url("test-call-id")
        assert "localhost:3000" in url
        assert "test-call-id" in url

    def test_custom_grafana_url(self):
        """Test URL building with custom Grafana URL."""
        url = build_tempo_url("test-call-id", grafana_url="https://grafana.example.com")
        assert "grafana.example.com" in url
        assert "test-call-id" in url


class TestExtractMessageContent:
    """Test message content extraction."""

    @pytest.mark.parametrize(
        "msg,expected",
        [
            ({"content": "Hello world"}, "Hello world"),
            ({"content": ""}, ""),
            ({}, ""),
            (
                {"content": [{"type": "text", "text": "First"}, {"type": "text", "text": "Second"}]},
                "First Second",
            ),
            (
                {"content": [{"type": "text", "text": "Text"}, {"type": "image", "url": "http://..."}]},
                "Text",
            ),
        ],
    )
    def test_extract_content(self, msg, expected):
        """Test extracting content from various message formats."""
        assert extract_message_content(msg) == expected


class TestComputeRequestDiff:
    """Test request diff computation."""

    def test_no_changes(self):
        """Test diff when nothing changed."""
        original = final = {
            "model": "gpt-4",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "Hello"}],
        }

        diff = compute_request_diff(original, final)

        assert not diff.model_changed
        assert not diff.max_tokens_changed
        assert len(diff.messages) == 1
        assert not diff.messages[0].changed

    @pytest.mark.parametrize(
        "field,orig_value,final_value,changed_flag",
        [
            ("model", "gpt-4", "gpt-3.5-turbo", "model_changed"),
            ("max_tokens", 100, 200, "max_tokens_changed"),
        ],
    )
    def test_field_changes(self, field, orig_value, final_value, changed_flag):
        """Test diff when specific fields change."""
        original = {field: orig_value, "messages": []}
        final = {field: final_value, "messages": []}

        diff = compute_request_diff(original, final)

        assert getattr(diff, changed_flag)
        assert getattr(diff, f"original_{field}") == orig_value
        assert getattr(diff, f"final_{field}") == final_value

    def test_message_content_changed(self):
        """Test diff when message content changed."""
        original = {"messages": [{"role": "user", "content": "Original"}]}
        final = {"messages": [{"role": "user", "content": "Modified"}]}

        diff = compute_request_diff(original, final)

        assert len(diff.messages) == 1
        assert diff.messages[0].changed
        assert diff.messages[0].original_content == "Original"
        assert diff.messages[0].final_content == "Modified"

    @pytest.mark.parametrize(
        "orig_count,final_count",
        [
            (1, 2),  # Messages added
            (2, 1),  # Messages removed
        ],
    )
    def test_message_count_changes(self, orig_count, final_count):
        """Test diff when messages are added or removed."""
        original = {"messages": [{"role": "user", "content": f"Msg{i}"} for i in range(orig_count)]}
        final = {"messages": [{"role": "user", "content": f"Msg{i}"} for i in range(final_count)]}

        diff = compute_request_diff(original, final)

        assert len(diff.messages) == max(orig_count, final_count)


class TestComputeResponseDiff:
    """Test response diff computation."""

    def test_no_changes(self):
        """Test diff when nothing changed."""
        original = final = {"choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}]}

        diff = compute_response_diff(original, final)

        assert not diff.content_changed
        assert not diff.finish_reason_changed

    @pytest.mark.parametrize(
        "orig_content,final_content",
        [
            ("Original", "Modified"),
            ("", "Added"),
            ("Removed", ""),
        ],
    )
    def test_content_changes(self, orig_content, final_content):
        """Test diff when content changes."""
        original = {"choices": [{"message": {"content": orig_content}}]}
        final = {"choices": [{"message": {"content": final_content}}]}

        diff = compute_response_diff(original, final)

        assert diff.content_changed == (orig_content != final_content)
        assert diff.original_content == orig_content
        assert diff.final_content == final_content

    def test_finish_reason_changed(self):
        """Test diff when finish_reason changed."""
        original = {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}
        final = {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}

        diff = compute_response_diff(original, final)

        assert diff.finish_reason_changed
        assert diff.original_finish_reason == "stop"
        assert diff.final_finish_reason == "length"


class TestFetchCallEvents:
    """Test fetching call events from database."""

    @pytest.mark.asyncio
    async def test_successful_fetch(self):
        """Test successful event fetching."""
        mock_row = {
            "call_id": "test-call-id",
            "event_type": "v2_request",
            "created_at": datetime(2025, 10, 20, 10, 0, 0),
            "payload": {"data": "test"},
            "session_id": "test-session-id",
        }

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [mock_row]

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await fetch_call_events("test-call-id", mock_pool)

        assert result.call_id == "test-call-id"
        assert len(result.events) == 1
        assert result.events[0].event_type == "v2_request"
        assert result.tempo_trace_url is not None

    @pytest.mark.asyncio
    async def test_no_events_found(self):
        """Test error when no events found."""
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        with pytest.raises(ValueError, match="No events found"):
            await fetch_call_events("nonexistent-id", mock_pool)


class TestFetchCallDiff:
    """Test fetching and computing call diffs."""

    @pytest.mark.asyncio
    async def test_successful_diff(self):
        """Test successful diff computation."""
        mock_request_row = {
            "call_id": "test-call-id",
            "event_type": "v2_request",
            "payload": {
                "data": {
                    "original": {"model": "gpt-4", "messages": []},
                    "final": {"model": "gpt-3.5-turbo", "messages": []},
                }
            },
        }

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [mock_request_row]

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await fetch_call_diff("test-call-id", mock_pool)

        assert result.call_id == "test-call-id"
        assert result.request is not None
        assert result.request.model_changed
        assert result.tempo_trace_url is not None

    @pytest.mark.asyncio
    async def test_both_request_and_response(self):
        """Test diff with both request and response events."""
        mock_rows = [
            {
                "call_id": "test-call-id",
                "event_type": "v2_request",
                "payload": {
                    "data": {
                        "original": {"model": "gpt-4", "messages": []},
                        "final": {"model": "gpt-4", "messages": []},
                    }
                },
            },
            {
                "call_id": "test-call-id",
                "event_type": "v2_response",
                "payload": {
                    "response": {
                        "original": {"choices": [{"message": {"content": "A"}}]},
                        "final": {"choices": [{"message": {"content": "B"}}]},
                    }
                },
            },
        ]

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = mock_rows

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await fetch_call_diff("test-call-id", mock_pool)

        assert result.request is not None
        assert result.response is not None
        assert result.response.content_changed

    @pytest.mark.asyncio
    async def test_no_events_found(self):
        """Test error when no events found."""
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        with pytest.raises(ValueError, match="No events found"):
            await fetch_call_diff("nonexistent-id", mock_pool)


class TestFetchRecentCalls:
    """Test fetching recent calls."""

    @pytest.mark.asyncio
    async def test_successful_fetch(self):
        """Test successful call listing."""
        mock_rows = [
            {
                "call_id": f"call-{i}",
                "event_count": i + 2,
                "latest": datetime(2025, 10, 20, 10 - i, 0, 0),
                "session_id": f"session-{i}" if i == 0 else None,
            }
            for i in range(2)
        ]

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = mock_rows

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await fetch_recent_calls(limit=10, db_pool=mock_pool)

        assert result.total == 2
        assert len(result.calls) == 2
        assert result.calls[0].call_id == "call-0"
        assert result.calls[0].event_count == 2

    @pytest.mark.asyncio
    async def test_empty_result(self):
        """Test when no calls found."""
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await fetch_recent_calls(limit=10, db_pool=mock_pool)

        assert result.total == 0
        assert result.calls == []
