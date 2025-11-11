# ABOUTME: Unit tests for V2 debug route handlers
# ABOUTME: Tests HTTP layer (dependency injection, error handling, status codes)

"""Tests for V2 debug route handlers.

These tests focus on the HTTP layer - ensuring routes properly:
- Handle dependency injection
- Convert service exceptions to appropriate HTTP status codes
- Return correct response models
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from luthien_proxy.debug.models import (
    CallDiffResponse,
    CallEventsResponse,
    CallListResponse,
)
from luthien_proxy.debug.routes import (
    get_call_diff,
    get_call_events,
    list_recent_calls,
)


class TestGetCallEventsRoute:
    """Test get_call_events route handler."""

    @pytest.mark.asyncio
    async def test_no_db_pool(self):
        """Test 503 error when db_pool is None."""
        with pytest.raises(HTTPException) as exc_info:
            await get_call_events("test-call-id", db_pool=None)

        assert exc_info.value.status_code == 503
        assert "not configured" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_call_not_found(self):
        """Test 404 error when call_id not found."""
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        with pytest.raises(HTTPException) as exc_info:
            await get_call_events("nonexistent-id", db_pool=mock_pool)

        assert exc_info.value.status_code == 404
        assert "No events found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_successful_response(self):
        """Test successful response returns CallEventsResponse."""
        mock_row = {
            "call_id": "test-call-id",
            "event_type": "v2_request",
            "sequence": 1,
            "created_at": datetime(2025, 10, 20, 10, 0, 0),
            "payload": {"data": "test"},
        }

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = [mock_row]

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await get_call_events("test-call-id", db_pool=mock_pool)

        assert isinstance(result, CallEventsResponse)
        assert result.call_id == "test-call-id"
        assert len(result.events) == 1

    @pytest.mark.asyncio
    async def test_database_error(self):
        """Test 500 error when database query fails."""
        mock_conn = AsyncMock()
        mock_conn.fetch.side_effect = Exception("Database connection failed")

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        with pytest.raises(HTTPException) as exc_info:
            await get_call_events("test-call-id", db_pool=mock_pool)

        assert exc_info.value.status_code == 500
        assert "Database error" in exc_info.value.detail


class TestGetCallDiffRoute:
    """Test get_call_diff route handler."""

    @pytest.mark.asyncio
    async def test_no_db_pool(self):
        """Test 503 error when db_pool is None."""
        with pytest.raises(HTTPException) as exc_info:
            await get_call_diff("test-call-id", db_pool=None)

        assert exc_info.value.status_code == 503
        assert "not configured" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_call_not_found(self):
        """Test 404 error when call_id not found."""
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        with pytest.raises(HTTPException) as exc_info:
            await get_call_diff("nonexistent-id", db_pool=mock_pool)

        assert exc_info.value.status_code == 404
        assert "No events found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_successful_response(self):
        """Test successful response returns CallDiffResponse."""
        mock_row = {
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
        mock_conn.fetch.return_value = [mock_row]

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await get_call_diff("test-call-id", db_pool=mock_pool)

        assert isinstance(result, CallDiffResponse)
        assert result.call_id == "test-call-id"

    @pytest.mark.asyncio
    async def test_database_error(self):
        """Test 500 error when database query fails."""
        mock_conn = AsyncMock()
        mock_conn.fetch.side_effect = Exception("Database connection failed")

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        with pytest.raises(HTTPException) as exc_info:
            await get_call_diff("test-call-id", db_pool=mock_pool)

        assert exc_info.value.status_code == 500
        assert "Database error" in exc_info.value.detail


class TestListRecentCallsRoute:
    """Test list_recent_calls route handler."""

    @pytest.mark.asyncio
    async def test_no_db_pool(self):
        """Test 503 error when db_pool is None."""
        with pytest.raises(HTTPException) as exc_info:
            await list_recent_calls(limit=10, db_pool=None)

        assert exc_info.value.status_code == 503
        assert "not configured" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_empty_result(self):
        """Test successful response with no calls."""
        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = []

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await list_recent_calls(limit=10, db_pool=mock_pool)

        assert isinstance(result, CallListResponse)
        assert result.total == 0
        assert result.calls == []

    @pytest.mark.asyncio
    async def test_successful_response(self):
        """Test successful response with calls."""
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

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await list_recent_calls(limit=10, db_pool=mock_pool)

        assert isinstance(result, CallListResponse)
        assert result.total == 2
        assert len(result.calls) == 2

    @pytest.mark.asyncio
    async def test_database_error(self):
        """Test 500 error when database query fails."""
        mock_conn = AsyncMock()
        mock_conn.fetch.side_effect = Exception("Database connection failed")

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        with pytest.raises(HTTPException) as exc_info:
            await list_recent_calls(limit=10, db_pool=mock_pool)

        assert exc_info.value.status_code == 500
        assert "Database error" in exc_info.value.detail
