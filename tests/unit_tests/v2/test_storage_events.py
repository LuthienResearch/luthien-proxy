"""Unit tests for V2 event emission helpers."""

from unittest.mock import Mock

from luthien_proxy.v2.storage.events import (
    emit_request_event,
    emit_response_event,
    reconstruct_full_response_from_chunks,
)


class StreamingResponseWrapper:
    """Wrapper that mimics StreamingResponse structure for testing."""

    def __init__(self, chunk):
        self.chunk = chunk


def test_emit_request_event_with_null_call_id(caplog):
    """Test that emit_request_event handles null call_id gracefully."""
    mock_db_pool = Mock()

    emit_request_event(
        call_id="",
        original_request={"model": "test"},
        final_request={"model": "test"},
        db_pool=mock_db_pool,
    )

    # Should log error and not call db
    assert "empty call_id" in caplog.text
    mock_db_pool.assert_not_called()


def test_emit_request_event_with_null_db_pool(caplog):
    """Test that emit_request_event handles null db_pool gracefully."""
    import logging

    caplog.set_level(logging.DEBUG)

    emit_request_event(
        call_id="test-123",
        original_request={"model": "test"},
        final_request={"model": "test"},
        db_pool=None,
    )

    # Should log debug and return
    assert "No db_pool provided" in caplog.text


def test_emit_response_event_with_null_call_id(caplog):
    """Test that emit_response_event handles null call_id gracefully."""
    mock_db_pool = Mock()

    emit_response_event(
        call_id="",
        original_response={"message": "test"},
        final_response={"message": "test"},
        db_pool=mock_db_pool,
    )

    # Should log error and not call db
    assert "empty call_id" in caplog.text
    mock_db_pool.assert_not_called()


def test_emit_response_event_with_null_db_pool(caplog):
    """Test that emit_response_event handles null db_pool gracefully."""
    import logging

    caplog.set_level(logging.DEBUG)

    emit_response_event(
        call_id="test-123",
        original_response={"message": "test"},
        final_response={"message": "test"},
        db_pool=None,
    )

    # Should log debug and return
    assert "No db_pool provided" in caplog.text


# === Tests for reconstruct_full_response_from_chunks ===


def test_reconstruct_empty_chunks():
    """Test reconstruction with empty chunk list."""
    result = reconstruct_full_response_from_chunks([])

    assert result["id"] == ""
    assert result["model"] == ""
    assert len(result["choices"]) == 1
    assert result["choices"][0]["message"]["role"] == "assistant"
    assert result["choices"][0]["message"]["content"] == ""
    assert result["choices"][0]["finish_reason"] == "stop"
    assert result["usage"] is None


def test_reconstruct_single_chunk_with_content(make_streaming_chunk):
    """Test reconstruction with single chunk containing content."""
    chunk = make_streaming_chunk(content="Hello", id="chatcmpl-123", model="gpt-4")
    wrapper = StreamingResponseWrapper(chunk)

    result = reconstruct_full_response_from_chunks([wrapper])

    assert result["id"] == "chatcmpl-123"
    assert result["model"] == "gpt-4"
    assert result["choices"][0]["message"]["content"] == "Hello"
    assert result["choices"][0]["finish_reason"] == "stop"


def test_reconstruct_multiple_chunks_accumulate_content(make_streaming_chunk):
    """Test that content accumulates from multiple chunks."""
    chunk1 = make_streaming_chunk(content="Hello ", id="chatcmpl-456", model="claude-opus-4-1")
    chunk2 = make_streaming_chunk(content="world", id="chatcmpl-456", model="claude-opus-4-1")
    chunk3 = make_streaming_chunk(content="!", id="chatcmpl-456", model="claude-opus-4-1", finish_reason="stop")

    chunks = [StreamingResponseWrapper(chunk1), StreamingResponseWrapper(chunk2), StreamingResponseWrapper(chunk3)]

    result = reconstruct_full_response_from_chunks(chunks)

    assert result["id"] == "chatcmpl-456"
    assert result["model"] == "claude-opus-4-1"
    assert result["choices"][0]["message"]["content"] == "Hello world!"
    assert result["choices"][0]["finish_reason"] == "stop"


def test_reconstruct_chunks_without_wrapper(make_streaming_chunk):
    """Test reconstruction with raw chunks (not wrapped)."""
    chunk = make_streaming_chunk(content="Direct chunk", id="test-id", model="test-model", finish_reason="stop")

    # Pass raw chunk (not wrapped)
    result = reconstruct_full_response_from_chunks([chunk])

    assert result["id"] == "test-id"
    assert result["model"] == "test-model"
    assert result["choices"][0]["message"]["content"] == "Direct chunk"


def test_reconstruct_chunks_with_missing_content(make_streaming_chunk):
    """Test reconstruction when some chunks have no content."""
    chunk1 = make_streaming_chunk(content="Start", id="id-1", model="model-1")
    chunk2 = make_streaming_chunk(content=None, id="id-1", model="model-1")  # None content should be skipped
    chunk3 = make_streaming_chunk(content=" End", id="id-1", model="model-1", finish_reason="stop")

    chunks = [chunk1, chunk2, chunk3]
    result = reconstruct_full_response_from_chunks(chunks)

    assert result["choices"][0]["message"]["content"] == "Start End"
