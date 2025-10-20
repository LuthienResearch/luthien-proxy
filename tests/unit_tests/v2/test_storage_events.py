"""Unit tests for V2 event emission helpers."""

from unittest.mock import Mock

from luthien_proxy.v2.storage.events import (
    emit_request_event,
    emit_response_event,
    reconstruct_full_response_from_chunks,
)


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


def test_reconstruct_single_chunk_with_content():
    """Test reconstruction with single chunk containing content."""
    # Mock chunk structure
    mock_delta = Mock()
    mock_delta.content = "Hello"

    mock_choice = Mock()
    mock_choice.delta = mock_delta
    mock_choice.finish_reason = None

    mock_chunk = Mock()
    mock_chunk.id = "chatcmpl-123"
    mock_chunk.model = "gpt-4"
    mock_chunk.choices = [mock_choice]

    # Wrap in StreamingResponse-like object
    mock_wrapper = Mock()
    mock_wrapper.chunk = mock_chunk

    result = reconstruct_full_response_from_chunks([mock_wrapper])

    assert result["id"] == "chatcmpl-123"
    assert result["model"] == "gpt-4"
    assert result["choices"][0]["message"]["content"] == "Hello"
    assert result["choices"][0]["finish_reason"] == "stop"


def test_reconstruct_multiple_chunks_accumulate_content():
    """Test that content accumulates from multiple chunks."""
    chunks = []

    # First chunk with metadata
    delta1 = Mock()
    delta1.content = "Hello "
    choice1 = Mock()
    choice1.delta = delta1
    choice1.finish_reason = None

    chunk1 = Mock()
    chunk1.id = "chatcmpl-456"
    chunk1.model = "claude-opus-4-1"
    chunk1.choices = [choice1]

    wrapper1 = Mock()
    wrapper1.chunk = chunk1
    chunks.append(wrapper1)

    # Second chunk with more content
    delta2 = Mock()
    delta2.content = "world"
    choice2 = Mock()
    choice2.delta = delta2
    choice2.finish_reason = None

    chunk2 = Mock()
    chunk2.id = "chatcmpl-456"
    chunk2.model = "claude-opus-4-1"
    chunk2.choices = [choice2]

    wrapper2 = Mock()
    wrapper2.chunk = chunk2
    chunks.append(wrapper2)

    # Final chunk with finish_reason
    delta3 = Mock()
    delta3.content = "!"
    choice3 = Mock()
    choice3.delta = delta3
    choice3.finish_reason = "stop"

    chunk3 = Mock()
    chunk3.id = "chatcmpl-456"
    chunk3.model = "claude-opus-4-1"
    chunk3.choices = [choice3]

    wrapper3 = Mock()
    wrapper3.chunk = chunk3
    chunks.append(wrapper3)

    result = reconstruct_full_response_from_chunks(chunks)

    assert result["id"] == "chatcmpl-456"
    assert result["model"] == "claude-opus-4-1"
    assert result["choices"][0]["message"]["content"] == "Hello world!"
    assert result["choices"][0]["finish_reason"] == "stop"


def test_reconstruct_chunks_without_wrapper():
    """Test reconstruction with raw chunks (not wrapped)."""
    delta = Mock()
    delta.content = "Direct chunk"
    choice = Mock()
    choice.delta = delta
    choice.finish_reason = "stop"

    # Create chunk object with actual list for choices
    class MockChunk:
        def __init__(self):
            self.id = "test-id"
            self.model = "test-model"
            self.choices = [choice]

    mock_chunk = MockChunk()

    # Pass raw chunk (not wrapped)
    result = reconstruct_full_response_from_chunks([mock_chunk])

    assert result["id"] == "test-id"
    assert result["model"] == "test-model"
    assert result["choices"][0]["message"]["content"] == "Direct chunk"


def test_reconstruct_chunks_with_missing_content():
    """Test reconstruction when some chunks have no content."""
    # Chunk with content
    delta1 = Mock()
    delta1.content = "Start"
    choice1 = Mock()
    choice1.delta = delta1
    choice1.finish_reason = None

    class MockChunk1:
        def __init__(self):
            self.id = "id-1"
            self.model = "model-1"
            self.choices = [choice1]

    # Chunk with None content (should be skipped)
    delta2 = Mock()
    delta2.content = None
    choice2 = Mock()
    choice2.delta = delta2
    choice2.finish_reason = None

    class MockChunk2:
        def __init__(self):
            self.id = "id-1"
            self.model = "model-1"
            self.choices = [choice2]

    # Chunk with content
    delta3 = Mock()
    delta3.content = " End"
    choice3 = Mock()
    choice3.delta = delta3
    choice3.finish_reason = "stop"

    class MockChunk3:
        def __init__(self):
            self.id = "id-1"
            self.model = "model-1"
            self.choices = [choice3]

    chunks = [MockChunk1(), MockChunk2(), MockChunk3()]
    result = reconstruct_full_response_from_chunks(chunks)

    assert result["choices"][0]["message"]["content"] == "Start End"
