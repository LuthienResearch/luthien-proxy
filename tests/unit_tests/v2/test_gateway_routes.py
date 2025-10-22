# ABOUTME: Unit tests for V2 gateway routes and helper functions
# ABOUTME: Tests authentication, hashing, streaming utilities for OpenAI and Anthropic endpoints

"""Tests for V2 gateway routes."""

from unittest.mock import MagicMock, Mock, patch

import pytest

from luthien_proxy.v2.gateway_routes import (
    hash_api_key,
    stream_llm_chunks,
    stream_with_policy_control,
)


class TestAuthentication:
    """Test authentication helpers."""

    def test_hash_api_key(self):
        """Test API key hashing for logging."""
        key = "my-secret-key"
        hashed = hash_api_key(key)

        assert isinstance(hashed, str)
        assert len(hashed) == 16
        # Same key always produces same hash
        assert hash_api_key(key) == hashed
        # Different key produces different hash
        assert hash_api_key("different-key") != hashed

    def test_hash_api_key_different_lengths(self):
        """Test hashing works with various key lengths."""
        short = hash_api_key("abc")
        medium = hash_api_key("this-is-a-medium-key")
        long = hash_api_key("x" * 100)

        # All produce 16-character hashes
        assert len(short) == 16
        assert len(medium) == 16
        assert len(long) == 16
        # All different
        assert short != medium != long


class TestStreamingHelpers:
    """Test streaming helper functions."""

    @pytest.mark.asyncio
    async def test_stream_llm_chunks(self):
        """Test streaming chunks from LiteLLM."""

        mock_chunks = [Mock(), Mock(), Mock()]

        async def mock_acompletion(**kwargs):
            """Mock async generator for LiteLLM completion."""

            async def chunk_generator():
                for chunk in mock_chunks:
                    yield chunk

            # Return object with async iterator protocol
            return chunk_generator()

        with patch("luthien_proxy.v2.gateway_routes.litellm.acompletion", mock_acompletion):
            chunks = []
            async for chunk in stream_llm_chunks({"model": "gpt-4"}):
                chunks.append(chunk)

            assert chunks == mock_chunks

    @pytest.mark.asyncio
    async def test_stream_with_policy_control_basic(self):
        """Test policy-controlled streaming without format conversion."""
        # Create mock chunks
        mock_chunk1 = Mock()
        mock_chunk1.model_dump_json.return_value = '{"content":"chunk1"}'
        mock_chunk2 = Mock()
        mock_chunk2.model_dump_json.return_value = '{"content":"chunk2"}'

        async def mock_policy_stream(*args, **kwargs):
            """Mock policy stream."""
            yield mock_chunk1
            yield mock_chunk2

        mock_control_plane = MagicMock()
        mock_control_plane.process_streaming_response = mock_policy_stream

        chunks = []
        async for chunk in stream_with_policy_control(
            data={"model": "gpt-4"},
            call_id="test-call-id",
            control_plane=mock_control_plane,
            db_pool=None,
            redis_client=None,
        ):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert 'data: {"content":"chunk1"}' in chunks[0]
        assert 'data: {"content":"chunk2"}' in chunks[1]

    @pytest.mark.asyncio
    async def test_stream_with_policy_control_dict_chunks(self):
        """Test policy streaming with dict chunks."""

        async def mock_policy_stream(*args, **kwargs):
            """Mock policy stream returning dicts."""
            yield {"type": "chunk", "text": "hello"}
            yield {"type": "chunk", "text": "world"}

        mock_control_plane = MagicMock()
        mock_control_plane.process_streaming_response = mock_policy_stream

        chunks = []
        async for chunk in stream_with_policy_control(
            data={"model": "gpt-4"},
            call_id="test-call-id",
            control_plane=mock_control_plane,
            db_pool=None,
            redis_client=None,
        ):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert "chunk" in chunks[0]
        assert "hello" in chunks[0]

    @pytest.mark.asyncio
    async def test_stream_with_policy_control_format_converter(self):
        """Test policy streaming with format conversion."""

        def format_converter(chunk):
            """Simple test converter."""
            return {"converted": True, "original": chunk}

        async def mock_policy_stream(*args, **kwargs):
            """Mock policy stream."""
            yield "chunk1"

        mock_control_plane = MagicMock()
        mock_control_plane.process_streaming_response = mock_policy_stream

        chunks = []
        async for chunk in stream_with_policy_control(
            data={"model": "gpt-4"},
            call_id="test-call-id",
            control_plane=mock_control_plane,
            db_pool=None,
            redis_client=None,
            format_converter=format_converter,
        ):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert "converted" in chunks[0].lower()
        assert "true" in chunks[0].lower()

    @pytest.mark.asyncio
    async def test_stream_with_policy_control_error(self):
        """Test error handling in policy streaming."""

        async def mock_policy_stream(*args, **kwargs):
            """Mock policy stream that raises error."""
            yield "chunk1"
            raise ValueError("Test error")

        mock_control_plane = MagicMock()
        mock_control_plane.process_streaming_response = mock_policy_stream

        chunks = []
        async for chunk in stream_with_policy_control(
            data={"model": "gpt-4"},
            call_id="test-call-id",
            control_plane=mock_control_plane,
            db_pool=None,
            redis_client=None,
        ):
            chunks.append(chunk)

        # Should get first chunk, then error message
        assert len(chunks) >= 1
        # Last chunk should be error
        assert "error" in chunks[-1].lower()
