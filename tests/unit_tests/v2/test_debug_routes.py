# ABOUTME: Unit tests for V2 debug routes
# ABOUTME: Tests query endpoints and database interactions

"""Tests for V2 debug routes."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from luthien_proxy.v2.debug.routes import (
    get_call_diff,
    get_call_events,
    list_recent_calls,
)


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

        mock_pool = MagicMock()
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

        mock_pool = MagicMock()
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

        mock_pool = MagicMock()
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

        mock_pool = MagicMock()
        mock_pool.connection.return_value.__aenter__.return_value = mock_conn

        result = await list_recent_calls(limit=10, db_pool=mock_pool)

        assert result.total == 2
        assert len(result.calls) == 2
        assert result.calls[0].call_id == "call-1"
        assert result.calls[0].event_count == 2
        assert result.calls[1].call_id == "call-2"
        assert result.calls[1].event_count == 4
