"""Tests for event emitter and safe serialization."""

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

from luthien_proxy.observability.emitter import (
    EventEmitter,
    NullEventEmitter,
    _safe_serialize,
)


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
