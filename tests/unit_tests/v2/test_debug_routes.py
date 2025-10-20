# ABOUTME: Unit tests for V2 debug routes
# ABOUTME: Tests query endpoints, diff computation, and database interactions

"""Tests for V2 debug routes."""

from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from luthien_proxy.v2.debug.routes import (
    _compute_request_diff,
    _compute_response_diff,
    _extract_message_content,
    get_call_diff,
    get_call_events,
    list_recent_calls,
)


class TestExtractMessageContent:
    """Test message content extraction."""

    def test_string_content(self):
        """Test extracting string content."""
        msg = {"role": "user", "content": "Hello world"}
        assert _extract_message_content(msg) == "Hello world"

    def test_list_content_with_text_blocks(self):
        """Test extracting content from list of blocks."""
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "Hello"},
                {"type": "text", "text": "world"},
            ],
        }
        assert _extract_message_content(msg) == "Hello world"

    def test_empty_content(self):
        """Test handling empty content."""
        msg = {"role": "user"}
        assert _extract_message_content(msg) == ""

    def test_non_string_content(self):
        """Test handling non-string content."""
        msg = {"role": "user", "content": 123}
        assert _extract_message_content(msg) == "123"


class TestComputeRequestDiff:
    """Test request diff computation."""

    def test_model_changed(self):
        """Test detecting model changes."""
        original = {"model": "gpt-4", "messages": []}
        final = {"model": "gpt-3.5-turbo", "messages": []}

        diff = _compute_request_diff(original, final)

        assert diff.model_changed is True
        assert diff.original_model == "gpt-4"
        assert diff.final_model == "gpt-3.5-turbo"

    def test_max_tokens_changed(self):
        """Test detecting max_tokens changes."""
        original = {"model": "gpt-4", "messages": [], "max_tokens": 1000}
        final = {"model": "gpt-4", "messages": [], "max_tokens": 500}

        diff = _compute_request_diff(original, final)

        assert diff.max_tokens_changed is True
        assert diff.original_max_tokens == 1000
        assert diff.final_max_tokens == 500

    def test_message_content_changed(self):
        """Test detecting message content changes."""
        original = {"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]}
        final = {"model": "gpt-4", "messages": [{"role": "user", "content": "Hi there"}]}

        diff = _compute_request_diff(original, final)

        assert len(diff.messages) == 1
        assert diff.messages[0].changed is True
        assert diff.messages[0].original_content == "Hello"
        assert diff.messages[0].final_content == "Hi there"

    def test_no_changes(self):
        """Test when nothing changed."""
        original = {"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]}
        final = {"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]}

        diff = _compute_request_diff(original, final)

        assert diff.model_changed is False
        assert diff.max_tokens_changed is False
        assert diff.messages[0].changed is False


class TestComputeResponseDiff:
    """Test response diff computation."""

    def test_content_changed(self):
        """Test detecting content changes."""
        original = {"choices": [{"message": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}]}
        final = {"choices": [{"message": {"role": "assistant", "content": "Hi there"}, "finish_reason": "stop"}]}

        diff = _compute_response_diff(original, final)

        assert diff.content_changed is True
        assert diff.original_content == "Hello"
        assert diff.final_content == "Hi there"

    def test_finish_reason_changed(self):
        """Test detecting finish_reason changes."""
        original = {"choices": [{"message": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}]}
        final = {"choices": [{"message": {"role": "assistant", "content": "Hello"}, "finish_reason": "length"}]}

        diff = _compute_response_diff(original, final)

        assert diff.finish_reason_changed is True
        assert diff.original_finish_reason == "stop"
        assert diff.final_finish_reason == "length"

    def test_no_changes(self):
        """Test when nothing changed."""
        original = {"choices": [{"message": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}]}
        final = {"choices": [{"message": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}]}

        diff = _compute_response_diff(original, final)

        assert diff.content_changed is False
        assert diff.finish_reason_changed is False


class TestGetCallEvents:
    """Test get_call_events endpoint."""

    @pytest.mark.asyncio
    async def test_no_db_pool(self):
        """Test error when db_pool is None."""
        with pytest.raises(HTTPException) as exc_info:
            await get_call_events("test-call-id", db_pool=None)

        assert exc_info.value.status_code == 503
        assert "not configured" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_call_not_found(self):
        """Test error when call_id not found."""
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []

        mock_pool = Mock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        with pytest.raises(HTTPException) as exc_info:
            await get_call_events("nonexistent-id", db_pool=mock_pool)

        assert exc_info.value.status_code == 404
        assert "No events found" in exc_info.value.detail


class TestGetCallDiff:
    """Test get_call_diff endpoint."""

    @pytest.mark.asyncio
    async def test_no_db_pool(self):
        """Test error when db_pool is None."""
        with pytest.raises(HTTPException) as exc_info:
            await get_call_diff("test-call-id", db_pool=None)

        assert exc_info.value.status_code == 503
        assert "not configured" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_call_not_found(self):
        """Test error when call_id not found."""
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []

        mock_pool = Mock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        with pytest.raises(HTTPException) as exc_info:
            await get_call_diff("nonexistent-id", db_pool=mock_pool)

        assert exc_info.value.status_code == 404
        assert "No events found" in exc_info.value.detail


class TestListRecentCalls:
    """Test list_recent_calls endpoint."""

    @pytest.mark.asyncio
    async def test_no_db_pool(self):
        """Test error when db_pool is None."""
        with pytest.raises(HTTPException) as exc_info:
            await list_recent_calls(limit=10, db_pool=None)

        assert exc_info.value.status_code == 503
        assert "not configured" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_empty_result(self):
        """Test when no calls found."""

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []

        mock_pool = Mock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await list_recent_calls(limit=10, db_pool=mock_pool)

        assert result.total == 0
        assert result.calls == []

    @pytest.mark.asyncio
    async def test_returns_calls(self):
        """Test successful call listing."""
        from datetime import datetime

        mock_row1 = {
            "call_id": "call-1",
            "event_count": 2,
            "latest": datetime(2025, 10, 20, 10, 0, 0),
        }
        mock_row2 = {
            "call_id": "call-2",
            "event_count": 4,
            "latest": datetime(2025, 10, 20, 9, 0, 0),
        }

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [mock_row1, mock_row2]

        mock_pool = Mock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await list_recent_calls(limit=10, db_pool=mock_pool)

        assert result.total == 2
        assert len(result.calls) == 2
        assert result.calls[0].call_id == "call-1"
        assert result.calls[0].event_count == 2
        assert result.calls[1].call_id == "call-2"
        assert result.calls[1].event_count == 4
