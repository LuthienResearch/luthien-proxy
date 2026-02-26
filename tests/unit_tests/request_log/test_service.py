"""Unit tests for request log service."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from luthien_proxy.request_log.models import (
    RequestLogDetailResponse,
    RequestLogEntry,
    RequestLogListResponse,
)
from luthien_proxy.request_log.service import (
    _parse_jsonb,
    _row_to_entry,
    get_transaction_logs,
    list_request_logs,
)


class TestParseJsonb:
    """Tests for _parse_jsonb."""

    def test_parse_jsonb_none_returns_none(self) -> None:
        """None input should return None."""
        result = _parse_jsonb(None)
        assert result is None

    def test_parse_jsonb_dict_returns_copy(self) -> None:
        """Dict input should return a dict copy."""
        input_dict = {"key": "value", "nested": {"inner": 42}}
        result = _parse_jsonb(input_dict)
        assert result == input_dict
        assert result is not input_dict  # Should be a copy

    def test_parse_jsonb_json_string_parses(self) -> None:
        """JSON string should be parsed to dict."""
        json_str = '{"key": "value", "number": 123}'
        result = _parse_jsonb(json_str)
        assert result == {"key": "value", "number": 123}

    def test_parse_jsonb_complex_json_string(self) -> None:
        """Complex nested JSON string should parse correctly."""
        json_str = '{"outer": {"inner": [1, 2, 3]}, "bool": true, "null": null}'
        result = _parse_jsonb(json_str)
        assert result == {"outer": {"inner": [1, 2, 3]}, "bool": True, "null": None}

    def test_parse_jsonb_non_dict_non_str_returns_none(self) -> None:
        """Non-dict, non-str input should return None."""
        assert _parse_jsonb(42) is None
        assert _parse_jsonb(3.14) is None
        assert _parse_jsonb([1, 2, 3]) is None
        assert _parse_jsonb(True) is None


class TestRowToEntry:
    """Tests for _row_to_entry."""

    def _make_row(
        self,
        **overrides: Any,
    ) -> dict[str, Any]:
        """Create a mock database row with defaults."""
        defaults = {
            "id": 1,
            "transaction_id": "txn-123",
            "session_id": "sess-456",
            "direction": "inbound",
            "http_method": "POST",
            "url": "http://example.com/api/chat",
            "request_headers": {"content-type": "application/json"},
            "request_body": {"messages": [{"role": "user", "content": "hello"}]},
            "response_status": 200,
            "response_headers": {"content-type": "application/json"},
            "response_body": {"choices": [{"message": {"content": "response"}}]},
            "started_at": datetime(2026, 2, 26, 10, 30, 0),
            "completed_at": datetime(2026, 2, 26, 10, 30, 5),
            "duration_ms": 5000.0,
            "model": "gpt-4",
            "is_streaming": False,
            "endpoint": "/chat/completions",
        }
        defaults.update(overrides)
        return defaults

    def test_row_to_entry_full_row(self) -> None:
        """Full row with all fields should convert correctly."""
        row = self._make_row()
        entry = _row_to_entry(row)

        assert isinstance(entry, RequestLogEntry)
        assert entry.id == "1"
        assert entry.transaction_id == "txn-123"
        assert entry.session_id == "sess-456"
        assert entry.direction == "inbound"
        assert entry.http_method == "POST"
        assert entry.url == "http://example.com/api/chat"
        assert entry.request_headers == {"content-type": "application/json"}
        assert entry.request_body == {"messages": [{"role": "user", "content": "hello"}]}
        assert entry.response_status == 200
        assert entry.response_headers == {"content-type": "application/json"}
        assert entry.response_body == {"choices": [{"message": {"content": "response"}}]}
        assert entry.started_at == "2026-02-26T10:30:00"
        assert entry.completed_at == "2026-02-26T10:30:05"
        assert entry.duration_ms == 5000.0
        assert entry.model == "gpt-4"
        assert entry.is_streaming is False
        assert entry.endpoint == "/chat/completions"

    def test_row_to_entry_none_optional_fields(self) -> None:
        """None optional fields should remain None."""
        row = self._make_row(
            session_id=None,
            http_method=None,
            url=None,
            request_headers=None,
            request_body=None,
            response_status=None,
            response_headers=None,
            response_body=None,
            completed_at=None,
            duration_ms=None,
            model=None,
            endpoint=None,
        )
        entry = _row_to_entry(row)

        assert entry.session_id is None
        assert entry.http_method is None
        assert entry.url is None
        assert entry.request_headers is None
        assert entry.request_body is None
        assert entry.response_status is None
        assert entry.response_headers is None
        assert entry.response_body is None
        assert entry.completed_at is None
        assert entry.duration_ms is None
        assert entry.model is None
        assert entry.endpoint is None

    def test_row_to_entry_datetime_to_isoformat(self) -> None:
        """Datetime objects should convert to ISO format strings."""
        row = self._make_row(
            started_at=datetime(2026, 2, 26, 14, 45, 30, 123456),
            completed_at=datetime(2026, 2, 26, 14, 45, 35, 654321),
        )
        entry = _row_to_entry(row)

        assert entry.started_at == "2026-02-26T14:45:30.123456"
        assert entry.completed_at == "2026-02-26T14:45:35.654321"

    def test_row_to_entry_jsonb_as_string(self) -> None:
        """JSONB fields returned as strings should be parsed."""
        row = self._make_row(
            request_headers='{"content-type": "application/json"}',
            request_body='{"query": "test"}',
            response_body='{"result": "ok"}',
        )
        entry = _row_to_entry(row)

        assert entry.request_headers == {"content-type": "application/json"}
        assert entry.request_body == {"query": "test"}
        assert entry.response_body == {"result": "ok"}

    def test_row_to_entry_id_conversion(self) -> None:
        """IDs from database should be converted to strings."""
        row = self._make_row(
            id=42,
            transaction_id=999,
            session_id=888,
        )
        entry = _row_to_entry(row)

        assert entry.id == "42"
        assert entry.transaction_id == "999"
        assert entry.session_id == "888"

    def test_row_to_entry_is_streaming_bool(self) -> None:
        """is_streaming should be converted to bool."""
        row = self._make_row(is_streaming=True)
        entry = _row_to_entry(row)
        assert entry.is_streaming is True

        row = self._make_row(is_streaming=False)
        entry = _row_to_entry(row)
        assert entry.is_streaming is False

    def test_row_to_entry_response_status_int(self) -> None:
        """response_status should be converted to int."""
        row = self._make_row(response_status=404)
        entry = _row_to_entry(row)
        assert entry.response_status == 404
        assert isinstance(entry.response_status, int)


class TestListRequestLogs:
    """Tests for list_request_logs."""

    def _make_row(self, **overrides: Any) -> dict[str, Any]:
        """Create a mock database row."""
        defaults = {
            "id": 1,
            "transaction_id": "txn-123",
            "session_id": "sess-456",
            "direction": "inbound",
            "http_method": "POST",
            "url": "http://example.com/api/chat",
            "request_headers": {"content-type": "application/json"},
            "request_body": {"prompt": "hello"},
            "response_status": 200,
            "response_headers": None,
            "response_body": None,
            "started_at": datetime(2026, 2, 26, 10, 0, 0),
            "completed_at": datetime(2026, 2, 26, 10, 0, 5),
            "duration_ms": 5000.0,
            "model": "gpt-4",
            "is_streaming": False,
            "endpoint": "/chat/completions",
        }
        defaults.update(overrides)
        return defaults

    def _make_mock_pool(
        self,
        count_result: dict[str, Any] | None = None,
        fetch_results: list[dict[str, Any]] | None = None,
    ) -> MagicMock:
        """Create a mocked DatabasePool."""
        if count_result is None:
            count_result = {"cnt": 0}
        if fetch_results is None:
            fetch_results = []

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = count_result
        mock_conn.fetch.return_value = fetch_results

        @asynccontextmanager
        async def mock_connection() -> Any:  # type: ignore[misc]
            yield mock_conn

        mock_pool = MagicMock()
        mock_pool.connection = mock_connection

        return mock_pool

    @pytest.mark.asyncio
    async def test_list_request_logs_empty(self) -> None:
        """Empty result should return empty list."""
        mock_pool = self._make_mock_pool(count_result={"cnt": 0}, fetch_results=[])

        result = await list_request_logs(mock_pool)

        assert isinstance(result, RequestLogListResponse)
        assert result.logs == []
        assert result.total == 0
        assert result.limit == 50
        assert result.offset == 0

    @pytest.mark.asyncio
    async def test_list_request_logs_with_results(self) -> None:
        """Results should be converted to RequestLogEntry objects."""
        rows = [
            self._make_row(id=1, transaction_id="txn-1"),
            self._make_row(id=2, transaction_id="txn-2"),
            self._make_row(id=3, transaction_id="txn-3"),
        ]
        mock_pool = self._make_mock_pool(count_result={"cnt": 3}, fetch_results=rows)

        result = await list_request_logs(mock_pool)

        assert len(result.logs) == 3
        assert result.total == 3
        assert all(isinstance(log, RequestLogEntry) for log in result.logs)
        assert result.logs[0].transaction_id == "txn-1"
        assert result.logs[1].transaction_id == "txn-2"
        assert result.logs[2].transaction_id == "txn-3"

    @pytest.mark.asyncio
    async def test_list_request_logs_limit_capped_at_200(self) -> None:
        """Limit should be capped at 200."""
        mock_pool = self._make_mock_pool(count_result={"cnt": 0}, fetch_results=[])

        result = await list_request_logs(mock_pool, limit=500)

        assert result.limit == 200

    @pytest.mark.asyncio
    async def test_list_request_logs_default_limit_50(self) -> None:
        """Default limit should be 50."""
        mock_pool = self._make_mock_pool(count_result={"cnt": 0}, fetch_results=[])

        result = await list_request_logs(mock_pool)

        assert result.limit == 50

    @pytest.mark.asyncio
    async def test_list_request_logs_respects_offset(self) -> None:
        """Offset should be preserved in response."""
        mock_pool = self._make_mock_pool(count_result={"cnt": 100}, fetch_results=[])

        result = await list_request_logs(mock_pool, offset=25)

        assert result.offset == 25

    @pytest.mark.asyncio
    async def test_list_request_logs_direction_filter(self) -> None:
        """Direction filter should be applied to query."""
        rows = [self._make_row(direction="inbound")]
        mock_pool = self._make_mock_pool(count_result={"cnt": 1}, fetch_results=rows)

        result = await list_request_logs(mock_pool, direction="inbound")

        # Verify the result was returned correctly
        assert len(result.logs) == 1
        assert result.logs[0].direction == "inbound"

    @pytest.mark.asyncio
    async def test_list_request_logs_endpoint_filter(self) -> None:
        """Endpoint filter should be applied."""
        rows = [self._make_row(endpoint="/chat/completions")]
        mock_pool = self._make_mock_pool(count_result={"cnt": 1}, fetch_results=rows)

        result = await list_request_logs(mock_pool, endpoint="/chat/completions")

        assert len(result.logs) == 1

    @pytest.mark.asyncio
    async def test_list_request_logs_session_id_filter(self) -> None:
        """Session ID filter should be applied."""
        rows = [self._make_row(session_id="sess-xyz")]
        mock_pool = self._make_mock_pool(count_result={"cnt": 1}, fetch_results=rows)

        result = await list_request_logs(mock_pool, session_id="sess-xyz")

        assert len(result.logs) == 1

    @pytest.mark.asyncio
    async def test_list_request_logs_status_filter(self) -> None:
        """Status filter should be applied."""
        rows = [self._make_row(response_status=404)]
        mock_pool = self._make_mock_pool(count_result={"cnt": 1}, fetch_results=rows)

        result = await list_request_logs(mock_pool, status=404)

        assert len(result.logs) == 1

    @pytest.mark.asyncio
    async def test_list_request_logs_model_filter(self) -> None:
        """Model filter should be applied."""
        rows = [self._make_row(model="claude-3")]
        mock_pool = self._make_mock_pool(count_result={"cnt": 1}, fetch_results=rows)

        result = await list_request_logs(mock_pool, model="claude-3")

        assert len(result.logs) == 1

    @pytest.mark.asyncio
    async def test_list_request_logs_multiple_filters(self) -> None:
        """Multiple filters should be combined."""
        rows = [
            self._make_row(
                direction="inbound",
                endpoint="/chat/completions",
                session_id="sess-123",
            )
        ]
        mock_pool = self._make_mock_pool(count_result={"cnt": 1}, fetch_results=rows)

        result = await list_request_logs(
            mock_pool,
            direction="inbound",
            endpoint="/chat/completions",
            session_id="sess-123",
        )

        assert len(result.logs) == 1

    @pytest.mark.asyncio
    async def test_list_request_logs_pagination(self) -> None:
        """Pagination parameters should be preserved."""
        mock_pool = self._make_mock_pool(count_result={"cnt": 250}, fetch_results=[])

        result = await list_request_logs(mock_pool, limit=100, offset=50)

        assert result.limit == 100
        assert result.offset == 50
        assert result.total == 250


class TestGetTransactionLogs:
    """Tests for get_transaction_logs."""

    def _make_row(self, **overrides: Any) -> dict[str, Any]:
        """Create a mock database row."""
        defaults = {
            "id": 1,
            "transaction_id": "txn-123",
            "session_id": "sess-456",
            "direction": "inbound",
            "http_method": "POST",
            "url": "http://example.com/api/chat",
            "request_headers": None,
            "request_body": {"prompt": "hello"},
            "response_status": None,
            "response_headers": None,
            "response_body": None,
            "started_at": datetime(2026, 2, 26, 10, 0, 0),
            "completed_at": None,
            "duration_ms": None,
            "model": None,
            "is_streaming": False,
            "endpoint": "/chat/completions",
        }
        defaults.update(overrides)
        return defaults

    def _make_mock_pool(
        self,
        fetch_results: list[dict[str, Any]] | None = None,
    ) -> MagicMock:
        """Create a mocked DatabasePool."""
        if fetch_results is None:
            fetch_results = []

        mock_conn = AsyncMock()
        mock_conn.fetch.return_value = fetch_results

        @asynccontextmanager
        async def mock_connection() -> Any:  # type: ignore[misc]
            yield mock_conn

        mock_pool = MagicMock()
        mock_pool.connection = mock_connection

        return mock_pool

    @pytest.mark.asyncio
    async def test_get_transaction_logs_inbound_and_outbound(self) -> None:
        """Should return inbound and outbound entries."""
        rows = [
            self._make_row(
                id=1,
                direction="inbound",
                session_id="sess-123",
            ),
            self._make_row(
                id=2,
                direction="outbound",
                session_id="sess-123",
                response_status=200,
                response_body={"result": "ok"},
            ),
        ]
        mock_pool = self._make_mock_pool(fetch_results=rows)

        result = await get_transaction_logs(mock_pool, "txn-123")

        assert isinstance(result, RequestLogDetailResponse)
        assert result.transaction_id == "txn-123"
        assert result.session_id == "sess-123"
        assert result.inbound is not None
        assert result.inbound.direction == "inbound"
        assert result.outbound is not None
        assert result.outbound.direction == "outbound"
        assert result.outbound.response_status == 200

    @pytest.mark.asyncio
    async def test_get_transaction_logs_only_inbound(self) -> None:
        """Should handle case with only inbound entry."""
        rows = [
            self._make_row(direction="inbound"),
        ]
        mock_pool = self._make_mock_pool(fetch_results=rows)

        result = await get_transaction_logs(mock_pool, "txn-123")

        assert result.inbound is not None
        assert result.outbound is None

    @pytest.mark.asyncio
    async def test_get_transaction_logs_only_outbound(self) -> None:
        """Should handle case with only outbound entry."""
        rows = [
            self._make_row(direction="outbound"),
        ]
        mock_pool = self._make_mock_pool(fetch_results=rows)

        result = await get_transaction_logs(mock_pool, "txn-123")

        assert result.inbound is None
        assert result.outbound is not None

    @pytest.mark.asyncio
    async def test_get_transaction_logs_no_logs_raises_value_error(self) -> None:
        """Should raise ValueError when no logs found."""
        mock_pool = self._make_mock_pool(fetch_results=[])

        with pytest.raises(ValueError) as exc_info:
            await get_transaction_logs(mock_pool, "nonexistent-txn")

        assert "No request logs found for transaction_id: nonexistent-txn" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_transaction_logs_session_id_from_entries(self) -> None:
        """Should extract session_id from entries."""
        rows = [
            self._make_row(direction="inbound", session_id="sess-abc"),
            self._make_row(direction="outbound", session_id="sess-abc"),
        ]
        mock_pool = self._make_mock_pool(fetch_results=rows)

        result = await get_transaction_logs(mock_pool, "txn-123")

        assert result.session_id == "sess-abc"

    @pytest.mark.asyncio
    async def test_get_transaction_logs_session_id_none_if_not_in_entries(
        self,
    ) -> None:
        """Session ID should be None if not in any entry."""
        rows = [
            self._make_row(direction="inbound", session_id=None),
            self._make_row(direction="outbound", session_id=None),
        ]
        mock_pool = self._make_mock_pool(fetch_results=rows)

        result = await get_transaction_logs(mock_pool, "txn-123")

        assert result.session_id is None

    @pytest.mark.asyncio
    async def test_get_transaction_logs_preserves_transaction_id(self) -> None:
        """Transaction ID should match input parameter."""
        rows = [self._make_row(direction="inbound")]
        mock_pool = self._make_mock_pool(fetch_results=rows)

        txn_id = "my-custom-txn-id"
        result = await get_transaction_logs(mock_pool, txn_id)

        assert result.transaction_id == txn_id
