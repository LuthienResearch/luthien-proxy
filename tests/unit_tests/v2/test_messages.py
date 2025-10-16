# ABOUTME: Unit tests for V2 messages module
# ABOUTME: Tests Request, FullResponse, and StreamingResponse message types

"""Tests for V2 message types."""

from unittest.mock import Mock

from luthien_proxy.v2.messages import FullResponse, Request, StreamingResponse


class TestRequest:
    """Test Request message type."""

    def test_create_basic_request(self):
        """Test creating a basic request."""
        req = Request(model="gpt-4", messages=[{"role": "user", "content": "Hello"}])

        assert req.model == "gpt-4"
        assert req.messages == [{"role": "user", "content": "Hello"}]
        assert req.stream is False
        assert req.max_tokens is None
        assert req.temperature is None

    def test_create_request_with_optional_fields(self):
        """Test creating request with optional fields."""
        req = Request(
            model="claude-3-opus",
            messages=[{"role": "user", "content": "Test"}],
            max_tokens=100,
            temperature=0.7,
            stream=True,
        )

        assert req.model == "claude-3-opus"
        assert req.max_tokens == 100
        assert req.temperature == 0.7
        assert req.stream is True

    def test_request_allows_extra_fields(self):
        """Test that Request allows extra fields (Pydantic extra='allow')."""
        req = Request(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hi"}],
            custom_field="custom_value",
            another_field=123,
        )

        # Extra fields should be accessible
        assert req.model_extra["custom_field"] == "custom_value"
        assert req.model_extra["another_field"] == 123

    def test_request_model_dump(self):
        """Test serializing Request to dict."""
        req = Request(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=50,
            stream=False,
        )

        data = req.model_dump(exclude_none=True)
        assert data["model"] == "gpt-4"
        assert data["messages"] == [{"role": "user", "content": "Hello"}]
        assert data["max_tokens"] == 50
        assert data["stream"] is False
        # None values should be excluded
        assert "temperature" not in data

    def test_request_from_dict(self):
        """Test creating Request from dict."""
        data = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "system", "content": "You are helpful"}],
            "max_tokens": 200,
        }

        req = Request(**data)
        assert req.model == "gpt-3.5-turbo"
        assert len(req.messages) == 1
        assert req.max_tokens == 200


class TestFullResponse:
    """Test FullResponse message type."""

    def test_create_full_response(self):
        """Test creating a FullResponse."""
        mock_response = Mock()
        mock_response.choices = [{"message": {"content": "Hello"}}]

        resp = FullResponse(response=mock_response)
        assert resp.response == mock_response

    def test_from_model_response(self):
        """Test creating FullResponse from ModelResponse."""
        mock_response = Mock()
        mock_response.id = "chatcmpl-123"
        mock_response.choices = [{"message": {"content": "Test"}}]

        resp = FullResponse.from_model_response(mock_response)
        assert resp.response == mock_response
        assert resp.response.id == "chatcmpl-123"

    def test_to_model_response(self):
        """Test extracting ModelResponse from FullResponse."""
        mock_response = Mock()
        mock_response.model = "gpt-4"

        resp = FullResponse(response=mock_response)
        extracted = resp.to_model_response()

        assert extracted == mock_response
        assert extracted.model == "gpt-4"

    def test_full_response_roundtrip(self):
        """Test roundtrip: ModelResponse -> FullResponse -> ModelResponse."""
        mock_response = Mock()
        mock_response.id = "test-id"
        mock_response.model = "claude-3-opus"

        # Wrap
        resp = FullResponse.from_model_response(mock_response)
        assert resp.response.id == "test-id"

        # Unwrap
        extracted = resp.to_model_response()
        assert extracted == mock_response
        assert extracted.model == "claude-3-opus"


class TestStreamingResponse:
    """Test StreamingResponse message type."""

    def test_create_streaming_response(self):
        """Test creating a StreamingResponse."""
        mock_chunk = Mock()
        mock_chunk.choices = [{"delta": {"content": "Hi"}}]

        resp = StreamingResponse(chunk=mock_chunk)
        assert resp.chunk == mock_chunk

    def test_from_model_response(self):
        """Test creating StreamingResponse from chunk."""
        mock_chunk = Mock()
        mock_chunk.id = "chatcmpl-chunk-1"
        mock_chunk.choices = [{"delta": {"content": "Test"}}]

        resp = StreamingResponse.from_model_response(mock_chunk)
        assert resp.chunk == mock_chunk
        assert resp.chunk.id == "chatcmpl-chunk-1"

    def test_to_model_response(self):
        """Test extracting chunk from StreamingResponse."""
        mock_chunk = Mock()
        mock_chunk.choices = [{"delta": {"content": "word"}}]

        resp = StreamingResponse(chunk=mock_chunk)
        extracted = resp.to_model_response()

        assert extracted == mock_chunk

    def test_streaming_response_roundtrip(self):
        """Test roundtrip: chunk -> StreamingResponse -> chunk."""
        mock_chunk = Mock()
        mock_chunk.id = "chunk-123"
        mock_chunk.choices = [{"delta": {"content": "test"}}]

        # Wrap
        resp = StreamingResponse.from_model_response(mock_chunk)
        assert resp.chunk.id == "chunk-123"

        # Unwrap
        extracted = resp.to_model_response()
        assert extracted == mock_chunk
        assert extracted.choices[0]["delta"]["content"] == "test"

    def test_multiple_streaming_responses(self):
        """Test creating multiple StreamingResponse objects."""
        chunks = []
        for i in range(3):
            mock_chunk = Mock()
            mock_chunk.id = f"chunk-{i}"
            mock_chunk.choices = [{"delta": {"content": f"word{i}"}}]
            chunks.append(StreamingResponse.from_model_response(mock_chunk))

        assert len(chunks) == 3
        assert chunks[0].chunk.id == "chunk-0"
        assert chunks[1].chunk.id == "chunk-1"
        assert chunks[2].chunk.id == "chunk-2"
