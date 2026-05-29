"""Tests for event emitter and safe serialization."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from pydantic import BaseModel

from luthien_proxy.observability.emitter import (
    EventEmitter,
    NullEventEmitter,
    _safe_serialize,
)


def _add_transaction_cm(mock_conn: AsyncMock) -> AsyncMock:
    """Give a mocked connection a working ``transaction()`` async CM.

    ``_write_db`` now wraps its writes in ``async with conn.transaction():``; a
    bare AsyncMock returns a non-context-manager, so mock-based tests must opt in
    to a real no-op CM. Returns the same mock for chaining.
    """

    @asynccontextmanager
    async def _txn() -> Any:
        yield

    mock_conn.transaction = _txn
    return mock_conn


class TestSafeSerialize:
    """Tests for _safe_serialize function."""

    def test_primitives_pass_through(self) -> None:
        """Primitive JSON types should pass through unchanged."""
        assert _safe_serialize(None) is None
        assert _safe_serialize(True) is True
        assert _safe_serialize(False) is False
        assert _safe_serialize(42) == 42
        assert _safe_serialize(3.14) == 3.14
        assert _safe_serialize("hello") == "hello"

    def test_datetime_converted_to_iso(self) -> None:
        """Datetime objects should be converted to ISO format strings."""
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        result = _safe_serialize(dt)
        assert result == "2024-01-15T10:30:00+00:00"

    def test_bytes_converted_to_base64(self) -> None:
        """Bytes should be converted to base64 with prefix."""
        data = b"hello world"
        result = _safe_serialize(data)
        assert result == "b64:aGVsbG8gd29ybGQ="

    def test_dict_recursively_serialized(self) -> None:
        """Dicts should be recursively serialized."""
        data = {
            "string": "value",
            "number": 42,
            "nested": {"datetime": datetime(2024, 1, 15, tzinfo=UTC)},
        }
        result = _safe_serialize(data)
        assert result == {
            "string": "value",
            "number": 42,
            "nested": {"datetime": "2024-01-15T00:00:00+00:00"},
        }

    def test_dict_non_string_keys_converted(self) -> None:
        """Non-string dict keys should be converted to strings."""
        data = {1: "one", 2: "two"}
        result = _safe_serialize(data)
        assert result == {"1": "one", "2": "two"}

    def test_list_recursively_serialized(self) -> None:
        """Lists should be recursively serialized."""
        data = [1, "two", datetime(2024, 1, 15, tzinfo=UTC)]
        result = _safe_serialize(data)
        assert result == [1, "two", "2024-01-15T00:00:00+00:00"]

    def test_tuple_converted_to_list(self) -> None:
        """Tuples should be converted to lists."""
        data = (1, 2, 3)
        result = _safe_serialize(data)
        assert result == [1, 2, 3]

    def test_set_converted_to_sorted_list(self) -> None:
        """Sets should be converted to sorted lists."""
        data = {"c", "a", "b"}
        result = _safe_serialize(data)
        assert result == ["a", "b", "c"]

    def test_pydantic_model_serialized(self) -> None:
        """Pydantic models should be serialized via model_dump."""

        class SampleModel(BaseModel):
            name: str
            value: int

        model = SampleModel(name="test", value=42)
        result = _safe_serialize(model)
        assert result == {"name": "test", "value": 42}

    def test_object_with_dict_serialized(self) -> None:
        """Objects with __dict__ should be serialized via their dict."""

        class CustomObject:
            def __init__(self) -> None:
                self.field1 = "value1"
                self.field2 = 123

        obj = CustomObject()
        result = _safe_serialize(obj)
        assert result == {"field1": "value1", "field2": 123}

    def test_unserializable_converted_to_string(self) -> None:
        """Unserializable objects should fall back to string representation."""

        class Unserializable:
            def __str__(self) -> str:
                return "<Unserializable object>"

            # Remove __dict__ to test the fallback
            __slots__ = ()

        obj = Unserializable()
        result = _safe_serialize(obj)
        assert result == "<Unserializable object>"

    def test_result_is_json_serializable(self) -> None:
        """The output should always be JSON-serializable."""
        complex_data: dict[str, Any] = {
            "datetime": datetime(2024, 1, 15, tzinfo=UTC),
            "bytes": b"binary data",
            "set": {1, 2, 3},
            "nested": {
                "tuple": (1, 2),
                "list": [datetime(2024, 1, 1, tzinfo=UTC)],
            },
        }
        result = _safe_serialize(complex_data)
        # This should not raise
        json_str = json.dumps(result)
        assert isinstance(json_str, str)


class TestNullEventEmitter:
    """Tests for NullEventEmitter."""

    def test_record_is_noop(self) -> None:
        """record() should silently discard events."""
        emitter = NullEventEmitter()
        # Should not raise
        emitter.record("tx-123", "test.event", {"key": "value"})


class TestEventEmitter:
    """Tests for EventEmitter."""

    @pytest.mark.asyncio
    async def test_emit_serializes_data(self) -> None:
        """emit() should serialize data before passing to sinks."""
        emitter = EventEmitter(stdout_enabled=False)

        # Patch _write_stdout to capture what's passed
        with patch.object(emitter, "_write_stdout", new_callable=AsyncMock) as mock:
            emitter._stdout_enabled = True
            await emitter.emit(
                "tx-123",
                "test.event",
                {"datetime": datetime(2024, 1, 15, tzinfo=UTC)},
            )

            mock.assert_called_once()
            call_args = mock.call_args
            data = call_args[0][2]  # Third positional arg is data
            assert data == {"datetime": "2024-01-15T00:00:00+00:00"}

    @pytest.mark.asyncio
    async def test_emit_handles_non_serializable_data(self) -> None:
        """emit() should handle non-JSON-serializable data gracefully."""
        emitter = EventEmitter(stdout_enabled=False)

        class CustomClass:
            __slots__ = ()

            def __str__(self) -> str:
                return "<CustomClass>"

        with patch.object(emitter, "_write_stdout", new_callable=AsyncMock) as mock:
            emitter._stdout_enabled = True
            await emitter.emit(
                "tx-123",
                "test.event",
                {"custom": CustomClass()},
            )

            mock.assert_called_once()
            data = mock.call_args[0][2]
            assert data == {"custom": "<CustomClass>"}

    @pytest.mark.asyncio
    async def test_record_creates_background_task(self) -> None:
        """record() should create a background task for emit()."""
        emitter = EventEmitter(stdout_enabled=False)

        with patch.object(emitter, "emit", new_callable=AsyncMock) as mock_emit:
            emitter.record("tx-123", "test.event", {"key": "value"})

            # Give the background task a chance to run
            await asyncio.sleep(0)

            mock_emit.assert_called_once_with("tx-123", "test.event", {"key": "value"})

    @pytest.mark.asyncio
    async def test_emit_writes_to_db_sink(self) -> None:
        """emit() should write events to the database when db_pool is provided.

        Regression test: TYPE_CHECKING imports previously caused NameError in
        _write_db because cast(DatabasePool, ...) evaluated DatabasePool at
        runtime, but it was only imported under TYPE_CHECKING.
        """
        mock_conn = _add_transaction_cm(AsyncMock())

        @asynccontextmanager
        async def fake_connection():
            yield mock_conn

        mock_pool = AsyncMock()
        mock_pool.connection = fake_connection

        emitter = EventEmitter(db_pool=mock_pool, stdout_enabled=False)
        await emitter.emit("tx-123", "test.event", {"key": "value"})

        # DB sink should have been called (INSERT into conversation_calls + events)
        assert mock_conn.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_emit_writes_to_event_publisher_sink(self) -> None:
        """emit() should publish events when event_publisher is provided."""
        mock_publisher = AsyncMock()

        emitter = EventEmitter(event_publisher=mock_publisher, stdout_enabled=False)
        await emitter.emit("tx-123", "test.event", {"key": "value"})

        mock_publisher.publish_event.assert_called_once_with(
            call_id="tx-123",
            event_type="test.event",
            data={"key": "value"},
        )

    @pytest.mark.asyncio
    async def test_write_db_increments_dropped_counter_on_db_error(self) -> None:
        """_write_db() increments dropped_db_writes on asyncpg errors."""
        mock_conn = _add_transaction_cm(AsyncMock())
        mock_conn.execute = AsyncMock(side_effect=asyncpg.PostgresError("connection lost"))

        @asynccontextmanager
        async def fake_connection():
            yield mock_conn

        mock_pool = AsyncMock()
        mock_pool.connection = fake_connection

        emitter = EventEmitter(db_pool=mock_pool, stdout_enabled=False)

        before = EventEmitter.dropped_db_writes
        await emitter.emit("tx-123", "test.event", {"key": "value"})
        assert EventEmitter.dropped_db_writes == before + 1

    @pytest.mark.asyncio
    async def test_write_db_increments_dropped_counter_on_os_error(self) -> None:
        """_write_db() increments dropped_db_writes on OSError."""
        mock_conn = _add_transaction_cm(AsyncMock())
        mock_conn.execute = AsyncMock(side_effect=OSError("connection refused"))

        @asynccontextmanager
        async def fake_connection():
            yield mock_conn

        mock_pool = AsyncMock()
        mock_pool.connection = fake_connection

        emitter = EventEmitter(db_pool=mock_pool, stdout_enabled=False)

        before = EventEmitter.dropped_db_writes
        await emitter.emit("tx-123", "test.event", {"key": "value"})
        assert EventEmitter.dropped_db_writes == before + 1

    @pytest.mark.asyncio
    async def test_write_db_does_not_catch_unrelated_exceptions(self) -> None:
        """_write_db() propagates non-DB exceptions."""
        mock_conn = _add_transaction_cm(AsyncMock())
        mock_conn.execute = AsyncMock(side_effect=ValueError("unexpected"))

        @asynccontextmanager
        async def fake_connection():
            yield mock_conn

        mock_pool = AsyncMock()
        mock_pool.connection = fake_connection

        emitter = EventEmitter(db_pool=mock_pool, stdout_enabled=False)

        # asyncio.gather with return_exceptions=True wraps exceptions,
        # so we check that the ValueError is returned (not swallowed)
        results = await asyncio.gather(
            emitter._write_db("tx-123", "test.event", {"key": "value"}, datetime.now(UTC)),
            return_exceptions=True,
        )
        assert any(isinstance(r, ValueError) for r in results)


class TestWriteDbAtomicity:
    """The three _write_db statements must commit (or roll back) as one unit."""

    @pytest.fixture
    async def pool(self):
        from luthien_proxy.utils.db import DatabasePool
        from luthien_proxy.utils.migration_check import check_migrations

        p = DatabasePool("sqlite://:memory:")
        await check_migrations(p)
        return p

    @pytest.mark.asyncio
    async def test_summary_failure_rolls_back_event_insert(self, pool) -> None:
        """If update_session_summary raises, the conversation_events row it would
        have accompanied must NOT remain committed — the whole write rolls back."""
        emitter = EventEmitter(db_pool=pool, stdout_enabled=False)
        data = {"session_id": "sess-1", "user_id": "u1", "payload": "x"}

        boom = RuntimeError("summary update blew up")
        with patch(
            "luthien_proxy.observability.emitter.update_session_summary",
            new_callable=AsyncMock,
            side_effect=boom,
        ):
            with pytest.raises(RuntimeError):
                await emitter._write_db("tx-1", "transaction.request_recorded", data, datetime.now(UTC))

        # The event insert (and the call upsert) must have been rolled back.
        async with pool.connection() as conn:
            events = await conn.fetch("SELECT * FROM conversation_events WHERE call_id = $1", "tx-1")
            calls = await conn.fetch("SELECT * FROM conversation_calls WHERE call_id = $1", "tx-1")
            summaries = await conn.fetch("SELECT * FROM session_summaries WHERE session_id = $1", "sess-1")
        assert events == []
        assert calls == []
        assert summaries == []

    @pytest.mark.asyncio
    async def test_happy_path_commits_all_three(self, pool) -> None:
        """When nothing fails, the event row and the summary row are both present."""
        emitter = EventEmitter(db_pool=pool, stdout_enabled=False)
        data = {
            "session_id": "sess-2",
            "user_id": "u2",
            "final_model": "claude-x",
            "final_request": {"messages": [{"role": "user", "content": "hi"}]},
        }
        await emitter._write_db("tx-2", "transaction.request_recorded", data, datetime.now(UTC))

        async with pool.connection() as conn:
            events = await conn.fetch("SELECT * FROM conversation_events WHERE call_id = $1", "tx-2")
            summaries = await conn.fetch("SELECT * FROM session_summaries WHERE session_id = $1", "sess-2")
        assert len(events) == 1
        assert len(summaries) == 1
        assert summaries[0]["event_count"] == 1
