"""Unit tests for request_log route handlers.

Tests the HTTP layer: dependency injection, error handling, status codes.
Routes are tested by calling handler functions directly (not via TestClient)
since admin auth is injected as a Depends() parameter.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from luthien_proxy.request_log.models import (
    RequestLogDetailResponse,
    RequestLogEntry,
    RequestLogListResponse,
)
from luthien_proxy.request_log.routes import get_transaction, list_logs

AUTH_TOKEN = "test-admin-key"


def _make_entry(**overrides) -> RequestLogEntry:
    """Build a RequestLogEntry with sensible defaults."""
    defaults = {
        "id": "entry-1",
        "transaction_id": "txn-1",
        "direction": "inbound",
        "started_at": "2026-01-01T00:00:00",
    }
    defaults.update(overrides)
    return RequestLogEntry(**defaults)


class TestListLogs:
    """Tests for the list_logs route handler."""

    @pytest.mark.asyncio
    async def test_no_db_pool_returns_503(self):
        with pytest.raises(HTTPException) as exc_info:
            await list_logs(
                limit=50,
                offset=0,
                direction=None,
                endpoint=None,
                session_id=None,
                status=None,
                model=None,
                after=None,
                before=None,
                search=None,
                _=AUTH_TOKEN,
                db_pool=None,
            )

        assert exc_info.value.status_code == 503
        assert "not configured" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_successful_list(self):
        expected = RequestLogListResponse(
            logs=[_make_entry()],
            total=1,
            limit=50,
            offset=0,
        )

        mock_pool = MagicMock()
        with patch(
            "luthien_proxy.request_log.routes.list_request_logs",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_service:
            result = await list_logs(
                limit=50,
                offset=0,
                direction=None,
                endpoint=None,
                session_id=None,
                status=None,
                model=None,
                after=None,
                before=None,
                search=None,
                _=AUTH_TOKEN,
                db_pool=mock_pool,
            )

        assert result == expected
        mock_service.assert_awaited_once_with(
            mock_pool,
            limit=50,
            offset=0,
            direction=None,
            endpoint=None,
            session_id=None,
            status=None,
            model=None,
            after=None,
            before=None,
            search=None,
        )

    @pytest.mark.asyncio
    async def test_filters_passed_through(self):
        expected = RequestLogListResponse(logs=[], total=0, limit=10, offset=5)

        mock_pool = MagicMock()
        with patch(
            "luthien_proxy.request_log.routes.list_request_logs",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_service:
            result = await list_logs(
                limit=10,
                offset=5,
                direction="inbound",
                endpoint="/v1/chat/completions",
                session_id="sess-1",
                status=200,
                model="gpt-4",
                after="2026-01-01T00:00:00",
                before="2026-02-01T00:00:00",
                search="hello",
                _=AUTH_TOKEN,
                db_pool=mock_pool,
            )

        assert result == expected
        mock_service.assert_awaited_once_with(
            mock_pool,
            limit=10,
            offset=5,
            direction="inbound",
            endpoint="/v1/chat/completions",
            session_id="sess-1",
            status=200,
            model="gpt-4",
            after="2026-01-01T00:00:00",
            before="2026-02-01T00:00:00",
            search="hello",
        )

    @pytest.mark.asyncio
    async def test_database_error_returns_500(self):
        mock_pool = MagicMock()
        with patch(
            "luthien_proxy.request_log.routes.list_request_logs",
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection lost"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await list_logs(
                    limit=50,
                    offset=0,
                    direction=None,
                    endpoint=None,
                    session_id=None,
                    status=None,
                    model=None,
                    after=None,
                    before=None,
                    search=None,
                    _=AUTH_TOKEN,
                    db_pool=mock_pool,
                )

        assert exc_info.value.status_code == 500
        assert "Database error" in exc_info.value.detail


class TestGetTransaction:
    """Tests for the get_transaction route handler."""

    @pytest.mark.asyncio
    async def test_no_db_pool_returns_503(self):
        with pytest.raises(HTTPException) as exc_info:
            await get_transaction("txn-1", _=AUTH_TOKEN, db_pool=None)

        assert exc_info.value.status_code == 503
        assert "not configured" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_successful_get(self):
        expected = RequestLogDetailResponse(
            transaction_id="txn-1",
            session_id="sess-1",
            inbound=_make_entry(direction="inbound"),
            outbound=_make_entry(id="entry-2", direction="outbound"),
        )

        mock_pool = MagicMock()
        with patch(
            "luthien_proxy.request_log.routes.get_transaction_logs",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_service:
            result = await get_transaction("txn-1", _=AUTH_TOKEN, db_pool=mock_pool)

        assert result == expected
        mock_service.assert_awaited_once_with(mock_pool, "txn-1")

    @pytest.mark.asyncio
    async def test_not_found_returns_404(self):
        mock_pool = MagicMock()
        with patch(
            "luthien_proxy.request_log.routes.get_transaction_logs",
            new_callable=AsyncMock,
            side_effect=ValueError("No request logs found for transaction_id: txn-missing"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_transaction("txn-missing", _=AUTH_TOKEN, db_pool=mock_pool)

        assert exc_info.value.status_code == 404
        assert "txn-missing" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_database_error_returns_500(self):
        mock_pool = MagicMock()
        with patch(
            "luthien_proxy.request_log.routes.get_transaction_logs",
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection lost"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_transaction("txn-1", _=AUTH_TOKEN, db_pool=mock_pool)

        assert exc_info.value.status_code == 500
        assert "Database error" in exc_info.value.detail
