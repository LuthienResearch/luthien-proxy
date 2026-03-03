"""Unit tests for RequestLogRecorder.

Tests cover:
1. create_recorder factory function with different configurations
2. NoOpRequestLogRecorder no-op behavior
3. RequestLogRecorder request/response recording and data storage
4. Header sanitization during recording
5. Duration calculation for inbound and outbound requests
6. Database persistence via flush() and _write_logs()
7. Error handling in flush() and database writes
8. Session ID inheritance from inbound to outbound logs
9. Background task creation in flush()
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from luthien_proxy.request_log.recorder import (
    NoOpRequestLogRecorder,
    RequestLogRecorder,
    _PendingLog,
    create_recorder,
)
from luthien_proxy.utils.db import DatabasePool


class Test_PendingLog:
    """Tests for _PendingLog dataclass."""

    def test_pending_log_defaults(self) -> None:
        """Verify _PendingLog initializes with sensible defaults."""
        log = _PendingLog(direction="inbound", transaction_id="txn-123")

        assert log.direction == "inbound"
        assert log.transaction_id == "txn-123"
        assert log.session_id is None
        assert log.http_method is None
        assert log.url is None
        assert log.request_headers is None
        assert log.request_body is None
        assert log.response_status is None
        assert log.response_headers is None
        assert log.response_body is None
        assert log.is_streaming is False
        assert log.endpoint is None
        assert log.error is None
        assert log.started_at > 0
        assert log.completed_at is None
        assert log.duration_ms is None

    def test_pending_log_started_at_is_captured(self) -> None:
        """Verify started_at captures time.time() at creation."""
        before = time.time()
        log = _PendingLog(direction="inbound", transaction_id="txn-123")
        after = time.time()

        assert before <= log.started_at <= after


class TestCreateRecorder:
    """Tests for create_recorder factory function."""

    def test_returns_noop_when_disabled(self) -> None:
        """Factory returns NoOpRequestLogRecorder when enabled=False."""
        db_pool = MagicMock(spec=DatabasePool)
        recorder = create_recorder(db_pool, "txn-123", enabled=False)

        assert isinstance(recorder, NoOpRequestLogRecorder)
        assert not isinstance(recorder, RequestLogRecorder) or isinstance(recorder, NoOpRequestLogRecorder)

    def test_returns_noop_when_db_pool_is_none(self) -> None:
        """Factory returns NoOpRequestLogRecorder when db_pool=None."""
        recorder = create_recorder(None, "txn-123", enabled=True)

        assert isinstance(recorder, NoOpRequestLogRecorder)

    def test_returns_real_recorder_when_enabled_and_pool_provided(self) -> None:
        """Factory returns RequestLogRecorder when enabled=True and db_pool provided."""
        db_pool = MagicMock(spec=DatabasePool)
        recorder = create_recorder(db_pool, "txn-123", enabled=True)

        assert isinstance(recorder, RequestLogRecorder)
        assert not isinstance(recorder, NoOpRequestLogRecorder)


class TestNoOpRequestLogRecorder:
    """Tests for NoOpRequestLogRecorder."""

    def test_record_inbound_request_is_noop(self) -> None:
        """record_inbound_request does not raise or store state."""
        recorder = NoOpRequestLogRecorder()
        # Should not raise
        recorder.record_inbound_request(
            method="POST",
            url="http://example.com/api",
            headers={"content-type": "application/json"},
            body={"key": "value"},
            session_id="sess-123",
            model="gpt-4",
            is_streaming=True,
            endpoint="/v1/chat",
        )

    def test_record_inbound_response_is_noop(self) -> None:
        """record_inbound_response does not raise or store state."""
        recorder = NoOpRequestLogRecorder()
        # Should not raise
        recorder.record_inbound_response(status=200, body={"ok": True}, headers={"x-custom": "value"})

    def test_record_outbound_request_is_noop(self) -> None:
        """record_outbound_request does not raise or store state."""
        recorder = NoOpRequestLogRecorder()
        # Should not raise
        recorder.record_outbound_request(
            body={"key": "value"},
            model="gpt-4",
            is_streaming=False,
            endpoint="/v1/chat",
        )

    def test_record_outbound_response_is_noop(self) -> None:
        """record_outbound_response does not raise or store state."""
        recorder = NoOpRequestLogRecorder()
        # Should not raise
        recorder.record_outbound_response(body={"result": "ok"}, status=200)

    def test_flush_is_noop(self) -> None:
        """flush does not raise or create background tasks."""
        recorder = NoOpRequestLogRecorder()
        # Should not raise
        recorder.flush()


class TestRequestLogRecorder:
    """Tests for RequestLogRecorder."""

    def test_initialization(self) -> None:
        """Verify RequestLogRecorder initializes with separate inbound/outbound logs."""
        db_pool = MagicMock(spec=DatabasePool)
        recorder = RequestLogRecorder(db_pool, "txn-123")

        assert recorder._db_pool is db_pool
        assert recorder._transaction_id == "txn-123"
        assert recorder._inbound.direction == "inbound"
        assert recorder._inbound.transaction_id == "txn-123"
        assert recorder._outbound.direction == "outbound"
        assert recorder._outbound.transaction_id == "txn-123"

    def test_record_inbound_request_stores_all_fields(self) -> None:
        """record_inbound_request populates inbound log with request details."""
        db_pool = MagicMock(spec=DatabasePool)
        recorder = RequestLogRecorder(db_pool, "txn-123")

        headers = {"content-type": "application/json", "authorization": "Bearer token"}
        body = {"messages": [{"role": "user", "content": "Hello"}]}

        recorder.record_inbound_request(
            method="POST",
            url="http://example.com/api",
            headers=headers,
            body=body,
            session_id="sess-456",
            model="gpt-4-turbo",
            is_streaming=True,
            endpoint="/v1/chat/completions",
        )

        assert recorder._inbound.http_method == "POST"
        assert recorder._inbound.url == "http://example.com/api"
        assert recorder._inbound.session_id == "sess-456"
        assert recorder._inbound.model == "gpt-4-turbo"
        assert recorder._inbound.is_streaming is True
        assert recorder._inbound.endpoint == "/v1/chat/completions"
        assert recorder._inbound.request_body == body

    def test_record_inbound_request_sanitizes_headers(self) -> None:
        """record_inbound_request sanitizes sensitive headers via sanitize_headers."""
        db_pool = MagicMock(spec=DatabasePool)
        recorder = RequestLogRecorder(db_pool, "txn-123")

        headers = {
            "content-type": "application/json",
            "authorization": "Bearer sk-secret123",
            "x-api-key": "key-value",
        }

        recorder.record_inbound_request(
            method="POST",
            url="http://example.com/api",
            headers=headers,
            body={},
        )

        # sanitize_headers should redact sensitive headers
        assert recorder._inbound.request_headers is not None
        assert recorder._inbound.request_headers["content-type"] == "application/json"
        assert recorder._inbound.request_headers["authorization"] == "[REDACTED]"
        assert recorder._inbound.request_headers["x-api-key"] == "[REDACTED]"

    def test_record_inbound_response_stores_status_and_body(self) -> None:
        """record_inbound_response populates response fields."""
        db_pool = MagicMock(spec=DatabasePool)
        recorder = RequestLogRecorder(db_pool, "txn-123")

        # Simulate inbound request first so we have a started_at time
        recorder.record_inbound_request(method="POST", url="http://example.com/api", headers={}, body={})

        # Record response
        response_body = {"choices": [{"message": {"content": "Hi"}}]}
        response_headers = {"content-type": "application/json", "authorization": "Bearer token"}
        recorder.record_inbound_response(status=200, body=response_body, headers=response_headers)

        assert recorder._inbound.response_status == 200
        assert recorder._inbound.response_body == response_body
        assert recorder._inbound.response_headers is not None
        # Verify headers were sanitized
        assert recorder._inbound.response_headers["authorization"] == "[REDACTED]"

    def test_record_inbound_response_calculates_duration(self) -> None:
        """record_inbound_response calculates duration_ms based on elapsed time."""
        db_pool = MagicMock(spec=DatabasePool)
        recorder = RequestLogRecorder(db_pool, "txn-123")

        # Set a known start time
        recorder._inbound.started_at = time.time() - 0.1  # 100ms ago
        recorder.record_inbound_response(status=200)

        assert recorder._inbound.duration_ms is not None
        # Duration should be approximately 100ms (allow some tolerance)
        assert 95 < recorder._inbound.duration_ms < 150

    def test_record_inbound_response_without_headers(self) -> None:
        """record_inbound_response handles missing headers gracefully."""
        db_pool = MagicMock(spec=DatabasePool)
        recorder = RequestLogRecorder(db_pool, "txn-123")

        recorder.record_inbound_response(status=204, body=None, headers=None)

        assert recorder._inbound.response_status == 204
        assert recorder._inbound.response_body is None
        assert recorder._inbound.response_headers is None

    def test_record_outbound_request_stores_body_and_metadata(self) -> None:
        """record_outbound_request populates outbound request fields."""
        db_pool = MagicMock(spec=DatabasePool)
        recorder = RequestLogRecorder(db_pool, "txn-123")

        # Set inbound session_id first
        recorder.record_inbound_request(
            method="POST",
            url="http://example.com/api",
            headers={},
            body={},
            session_id="sess-789",
            model="gpt-4",
            endpoint="/v1/completions",
        )

        # Record outbound request
        outbound_body = {"model": "gpt-4", "messages": []}
        recorder.record_outbound_request(
            body=outbound_body, model="gpt-4", is_streaming=False, endpoint="/v1/completions"
        )

        assert recorder._outbound.request_body == outbound_body
        assert recorder._outbound.model == "gpt-4"
        assert recorder._outbound.is_streaming is False
        assert recorder._outbound.endpoint == "/v1/completions"

    def test_record_outbound_request_inherits_session_id_from_inbound(self) -> None:
        """record_outbound_request copies session_id from inbound log."""
        db_pool = MagicMock(spec=DatabasePool)
        recorder = RequestLogRecorder(db_pool, "txn-123")

        # Set inbound with session_id
        recorder.record_inbound_request(
            method="POST",
            url="http://example.com/api",
            headers={},
            body={},
            session_id="my-session-id",
        )

        # Record outbound without specifying session_id
        recorder.record_outbound_request(body={})

        assert recorder._outbound.session_id == "my-session-id"

    def test_record_outbound_request_updates_started_at(self) -> None:
        """record_outbound_request sets started_at to current time."""
        db_pool = MagicMock(spec=DatabasePool)
        recorder = RequestLogRecorder(db_pool, "txn-123")

        before = time.time()
        recorder.record_outbound_request(body={})
        after = time.time()

        assert before <= recorder._outbound.started_at <= after

    def test_record_outbound_response_stores_status_and_body(self) -> None:
        """record_outbound_response populates outbound response fields."""
        db_pool = MagicMock(spec=DatabasePool)
        recorder = RequestLogRecorder(db_pool, "txn-123")

        # Start outbound request to set started_at
        recorder.record_outbound_request(body={})

        # Record response
        response_body = {"result": "ok"}
        recorder.record_outbound_response(body=response_body, status=200)

        assert recorder._outbound.response_status == 200
        assert recorder._outbound.response_body == response_body

    def test_record_outbound_response_calculates_duration(self) -> None:
        """record_outbound_response calculates duration_ms."""
        db_pool = MagicMock(spec=DatabasePool)
        recorder = RequestLogRecorder(db_pool, "txn-123")

        # Set a known start time
        recorder._outbound.started_at = time.time() - 0.05  # 50ms ago
        recorder.record_outbound_response(status=200)

        assert recorder._outbound.duration_ms is not None
        # Duration should be approximately 50ms
        assert 45 < recorder._outbound.duration_ms < 100

    def test_record_outbound_response_defaults_status_to_200(self) -> None:
        """record_outbound_response uses status=200 as default."""
        db_pool = MagicMock(spec=DatabasePool)
        recorder = RequestLogRecorder(db_pool, "txn-123")

        recorder._outbound.started_at = time.time()
        recorder.record_outbound_response()

        assert recorder._outbound.response_status == 200

    @pytest.mark.asyncio
    async def test_flush_creates_background_task(self) -> None:
        """flush() creates an async background task via asyncio.get_running_loop()."""
        db_pool = MagicMock(spec=DatabasePool)
        recorder = RequestLogRecorder(db_pool, "txn-123")

        # Mock _write_logs to verify it's called
        recorder._write_logs = AsyncMock()

        # flush() should schedule _write_logs as a background task
        recorder.flush()

        # Give the event loop a chance to process the task
        import asyncio

        await asyncio.sleep(0.01)

        # Verify _write_logs was called
        recorder._write_logs.assert_called_once()

    def test_flush_handles_no_running_loop(self) -> None:
        """flush() logs debug message when no event loop is running."""
        db_pool = MagicMock(spec=DatabasePool)
        recorder = RequestLogRecorder(db_pool, "txn-123")

        # Call flush() outside of an async context (no running loop)
        with patch("luthien_proxy.request_log.recorder.logger") as mock_logger:
            recorder.flush()

            # Verify debug message was logged
            mock_logger.debug.assert_called_once()
            call_args = mock_logger.debug.call_args[0][0]
            assert "No running event loop" in call_args

    @pytest.mark.asyncio
    async def test_write_logs_inserts_both_rows(self) -> None:
        """_write_logs() inserts both inbound and outbound log rows."""
        # Create a mock connection that tracks execute calls
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        # Create a mock db_pool
        db_pool = MagicMock(spec=DatabasePool)
        db_pool.connection = MagicMock()
        db_pool.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        db_pool.connection.return_value.__aexit__ = AsyncMock(return_value=None)

        recorder = RequestLogRecorder(db_pool, "txn-123")

        # Populate both logs with data
        recorder.record_inbound_request(
            method="POST",
            url="http://example.com/api",
            headers={"content-type": "application/json"},
            body={"test": "data"},
            session_id="sess-123",
            model="gpt-4",
        )
        recorder.record_inbound_response(status=200)

        recorder.record_outbound_request(body={"model": "gpt-4"})
        recorder.record_outbound_response(status=200)

        # Execute _write_logs
        await recorder._write_logs()

        # Verify execute was called twice (once per log)
        assert mock_conn.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_write_logs_constructs_correct_sql(self) -> None:
        """_write_logs() constructs the expected INSERT statement."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        db_pool = MagicMock(spec=DatabasePool)
        db_pool.connection = MagicMock()
        db_pool.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        db_pool.connection.return_value.__aexit__ = AsyncMock(return_value=None)

        recorder = RequestLogRecorder(db_pool, "txn-123")
        recorder.record_inbound_request(
            method="GET",
            url="http://example.com",
            headers={"x-header": "value"},
            body={"test": True},
        )
        recorder.record_inbound_response(status=404)

        await recorder._write_logs()

        # Verify the SQL contains expected table and columns
        call_args = mock_conn.execute.call_args_list[0]
        sql = call_args[0][0]
        assert "INSERT INTO request_logs" in sql
        assert "transaction_id" in sql
        assert "direction" in sql
        assert "response_status" in sql

    @pytest.mark.asyncio
    async def test_write_logs_serializes_json_fields(self) -> None:
        """_write_logs() JSON-serializes header and body fields."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        db_pool = MagicMock(spec=DatabasePool)
        db_pool.connection = MagicMock()
        db_pool.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        db_pool.connection.return_value.__aexit__ = AsyncMock(return_value=None)

        recorder = RequestLogRecorder(db_pool, "txn-123")
        recorder.record_inbound_request(
            method="POST",
            url="http://example.com",
            headers={"content-type": "application/json"},
            body={"nested": {"data": "structure"}},
        )
        recorder.record_inbound_response(status=200, body={"response": "data"})

        await recorder._write_logs()

        # Get the first call's arguments (inbound log)
        call_args = mock_conn.execute.call_args_list[0]
        args = call_args[0]

        # Args: [sql, txn_id, session_id, direction, method, url, headers, body, ...]
        # Headers should be JSON serialized
        headers_arg = args[6]
        assert headers_arg == json.dumps({"content-type": "application/json"})

        # Body should be JSON serialized
        body_arg = args[7]
        assert body_arg == json.dumps({"nested": {"data": "structure"}})

    @pytest.mark.asyncio
    async def test_write_logs_handles_none_json_fields(self) -> None:
        """_write_logs() passes None for missing JSON fields."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        db_pool = MagicMock(spec=DatabasePool)
        db_pool.connection = MagicMock()
        db_pool.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        db_pool.connection.return_value.__aexit__ = AsyncMock(return_value=None)

        recorder = RequestLogRecorder(db_pool, "txn-123")
        # Record inbound request with minimal data
        recorder.record_inbound_request(method="GET", url="http://example.com", headers={}, body=None)
        # Record response with no body
        recorder.record_inbound_response(status=204)

        await recorder._write_logs()

        # Get inbound log call
        call_args = mock_conn.execute.call_args_list[0]
        args = call_args[0]

        # Headers should be None (empty dict in record_inbound_request, but let's check)
        # Body should be None
        body_arg = args[7]
        assert body_arg is None

    @pytest.mark.asyncio
    async def test_write_logs_catches_and_logs_exceptions(self) -> None:
        """_write_logs() catches DB exceptions and logs them without raising."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=Exception("Database error"))

        db_pool = MagicMock(spec=DatabasePool)
        db_pool.connection = MagicMock()
        db_pool.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        db_pool.connection.return_value.__aexit__ = AsyncMock(return_value=None)

        recorder = RequestLogRecorder(db_pool, "txn-123")
        recorder.record_inbound_request(method="POST", url="http://example.com", headers={}, body={})

        with patch("luthien_proxy.request_log.recorder.logger") as mock_logger:
            # Should not raise
            await recorder._write_logs()

            # Verify exception was logged
            mock_logger.exception.assert_called_once()
            call_args = mock_logger.exception.call_args[0]
            assert "Failed to write request logs" in call_args[0]
            assert "txn-123" in call_args[1]

    @pytest.mark.asyncio
    async def test_write_logs_context_manager_cleanup(self) -> None:
        """_write_logs() properly uses connection context manager for cleanup."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_context_mgr = MagicMock()
        mock_context_mgr.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_context_mgr.__aexit__ = AsyncMock(return_value=None)

        db_pool = MagicMock(spec=DatabasePool)
        db_pool.connection = MagicMock(return_value=mock_context_mgr)

        recorder = RequestLogRecorder(db_pool, "txn-123")
        recorder.record_inbound_request(method="GET", url="http://example.com", headers={}, body={})

        await recorder._write_logs()

        # Verify context manager was properly used
        mock_context_mgr.__aenter__.assert_called()
        mock_context_mgr.__aexit__.assert_called()


class TestRequestLogRecorderIntegration:
    """Integration tests for typical recorder usage patterns."""

    @pytest.mark.asyncio
    async def test_complete_request_response_cycle(self) -> None:
        """Test a complete inbound + outbound request/response cycle."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        db_pool = MagicMock(spec=DatabasePool)
        db_pool.connection = MagicMock()
        db_pool.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        db_pool.connection.return_value.__aexit__ = AsyncMock(return_value=None)

        recorder = RequestLogRecorder(db_pool, "txn-456")

        # Simulate inbound request
        recorder.record_inbound_request(
            method="POST",
            url="http://client.example.com/api",
            headers={"authorization": "Bearer token", "content-type": "application/json"},
            body={"prompt": "Hello world"},
            session_id="user-session-789",
            model="gpt-4",
            is_streaming=True,
            endpoint="/v1/completions",
        )

        # Simulate inbound response (from proxy back to client)
        recorder.record_inbound_response(
            status=200,
            body={"choices": [{"text": "Hello!"}]},
            headers={"content-type": "application/json"},
        )

        # Simulate outbound request (from proxy to backend)
        recorder.record_outbound_request(
            body={"model": "gpt-4", "prompt": "Hello world"},
            model="gpt-4",
            is_streaming=True,
            endpoint="/v1/completions",
        )

        # Simulate outbound response (from backend to proxy)
        recorder.record_outbound_response(status=200, body={"choices": [{"text": "Hello!"}]})

        # Flush and write logs
        await recorder._write_logs()

        # Verify both logs were written
        assert mock_conn.execute.call_count == 2

        # Verify first call is inbound log
        inbound_call = mock_conn.execute.call_args_list[0]
        inbound_args = inbound_call[0]
        assert inbound_args[3] == "inbound"  # direction

        # Verify second call is outbound log
        outbound_call = mock_conn.execute.call_args_list[1]
        outbound_args = outbound_call[0]
        assert outbound_args[3] == "outbound"  # direction

        # Both should have same transaction_id
        assert inbound_args[1] == "txn-456"
        assert outbound_args[1] == "txn-456"

        # Outbound should inherit session_id from inbound
        assert inbound_args[2] == "user-session-789"
        assert outbound_args[2] == "user-session-789"

    def test_no_session_id_inbound_to_outbound_inheritance(self) -> None:
        """Verify outbound inherits None session_id if not set in inbound."""
        db_pool = MagicMock(spec=DatabasePool)
        recorder = RequestLogRecorder(db_pool, "txn-789")

        # Record inbound without session_id
        recorder.record_inbound_request(method="GET", url="http://example.com", headers={}, body={})

        # Record outbound
        recorder.record_outbound_request(body={})

        # Outbound session_id should be None
        assert recorder._outbound.session_id is None


class TestRecordOutboundRequestMethodUrl:
    """Tests for outbound request method and URL capture."""

    def test_outbound_request_captures_method_and_url(self) -> None:
        """record_outbound_request stores http_method and url."""
        db_pool = MagicMock(spec=DatabasePool)
        recorder = RequestLogRecorder(db_pool, "txn-123")

        recorder.record_outbound_request(
            method="POST",
            url="/v1/chat/completions",
            body={"model": "gpt-4"},
        )

        assert recorder._outbound.http_method == "POST"
        assert recorder._outbound.url == "/v1/chat/completions"

    def test_outbound_request_method_defaults_to_post(self) -> None:
        """method defaults to POST when not specified."""
        db_pool = MagicMock(spec=DatabasePool)
        recorder = RequestLogRecorder(db_pool, "txn-123")

        recorder.record_outbound_request(body={})

        assert recorder._outbound.http_method == "POST"

    def test_outbound_request_url_defaults_to_none(self) -> None:
        """url defaults to None when not specified."""
        db_pool = MagicMock(spec=DatabasePool)
        recorder = RequestLogRecorder(db_pool, "txn-123")

        recorder.record_outbound_request(body={})

        assert recorder._outbound.url is None


class TestErrorTracking:
    """Tests for error field on inbound/outbound responses."""

    def test_inbound_response_records_error(self) -> None:
        """record_inbound_response stores error string."""
        db_pool = MagicMock(spec=DatabasePool)
        recorder = RequestLogRecorder(db_pool, "txn-123")
        recorder._inbound.started_at = time.time()

        recorder.record_inbound_response(status=200, error="ConnectionError: stream interrupted")

        assert recorder._inbound.error == "ConnectionError: stream interrupted"

    def test_inbound_response_error_defaults_to_none(self) -> None:
        """error defaults to None for successful responses."""
        db_pool = MagicMock(spec=DatabasePool)
        recorder = RequestLogRecorder(db_pool, "txn-123")
        recorder._inbound.started_at = time.time()

        recorder.record_inbound_response(status=200)

        assert recorder._inbound.error is None

    def test_outbound_response_records_error(self) -> None:
        """record_outbound_response stores error string."""
        db_pool = MagicMock(spec=DatabasePool)
        recorder = RequestLogRecorder(db_pool, "txn-123")
        recorder._outbound.started_at = time.time()

        recorder.record_outbound_response(status=500, error="TimeoutError: backend timeout")

        assert recorder._outbound.error == "TimeoutError: backend timeout"
        assert recorder._outbound.response_status == 500

    def test_outbound_response_error_defaults_to_none(self) -> None:
        """error defaults to None for successful responses."""
        db_pool = MagicMock(spec=DatabasePool)
        recorder = RequestLogRecorder(db_pool, "txn-123")
        recorder._outbound.started_at = time.time()

        recorder.record_outbound_response(status=200)

        assert recorder._outbound.error is None


class TestBodyTruncation:
    """Tests for response body size limiting."""

    def test_serialize_body_none_returns_none(self) -> None:
        """None body returns None."""
        assert RequestLogRecorder._serialize_body(None) is None

    def test_serialize_body_small_body_passes_through(self) -> None:
        """Small bodies are serialized without truncation."""
        body = {"message": "hello"}
        result = RequestLogRecorder._serialize_body(body)
        assert result == json.dumps(body)

    def test_serialize_body_large_body_is_truncated(self) -> None:
        """Bodies exceeding MAX_BODY_BYTES are replaced with a truncation notice."""
        from luthien_proxy.request_log.recorder import MAX_BODY_BYTES

        large_body = {"data": "x" * (MAX_BODY_BYTES + 1)}
        result = RequestLogRecorder._serialize_body(large_body)

        parsed = json.loads(result)  # type: ignore[arg-type]
        assert parsed["_truncated"] is True
        assert parsed["_original_size_bytes"] > MAX_BODY_BYTES

    def test_serialize_body_exactly_at_limit_passes_through(self) -> None:
        """Bodies exactly at the limit are not truncated."""
        from luthien_proxy.request_log.recorder import MAX_BODY_BYTES

        # Build a body that serializes to exactly MAX_BODY_BYTES
        # json.dumps({"d": "xxx..."}) has overhead, so adjust
        overhead = len(json.dumps({"d": ""}))
        body = {"d": "x" * (MAX_BODY_BYTES - overhead)}
        serialized = json.dumps(body)
        assert len(serialized) == MAX_BODY_BYTES

        result = RequestLogRecorder._serialize_body(body)
        assert result == serialized
