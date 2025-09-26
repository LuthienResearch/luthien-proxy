"""Test suite for LiteLLM callback error handling and type normalization."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from litellm.types.utils import ModelResponseStream

from config.litellm_callback import LuthienCallback


@pytest.fixture
def callback():
    """Create a LuthienCallback instance for testing."""
    return LuthienCallback()


@pytest.mark.asyncio
async def test_apost_hook_handles_network_errors(callback):
    """Test that network errors are properly logged but don't raise."""
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")
        mock_client_class.return_value = mock_client

        result = await callback._apost_hook("test_hook", {"data": "test"})

        assert result is None  # Should return None on error


@pytest.mark.asyncio
async def test_apost_hook_handles_timeout(callback):
    """Test that timeout errors are properly handled."""
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.side_effect = httpx.TimeoutException("Request timeout")
        mock_client_class.return_value = mock_client

        result = await callback._apost_hook("test_hook", {"data": "test"})

        assert result is None


@pytest.mark.asyncio
async def test_apost_hook_handles_server_errors(callback):
    """Test that 5xx errors are logged as server errors."""
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client

        # Create a mock response with 500 status
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Internal Server Error",
            request=MagicMock(),
            response=mock_response
        )
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        result = await callback._apost_hook("test_hook", {"data": "test"})

        assert result is None


@pytest.mark.asyncio
async def test_apost_hook_handles_client_errors(callback):
    """Test that 4xx errors are logged as configuration errors."""
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client

        # Create a mock response with 400 status
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Bad Request",
            request=MagicMock(),
            response=mock_response
        )
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        result = await callback._apost_hook("test_hook", {"data": "test"})

        assert result is None


def test_normalize_stream_chunk_with_valid_original(callback):
    """Test normalization when original is already ModelResponseStream."""
    original = ModelResponseStream(
        id="test",
        choices=[{"index": 0, "delta": {"content": "test"}}],
        created=1234567890,
        model="gpt-3.5-turbo",
        object="chat.completion.chunk"
    )

    result = callback._normalize_stream_chunk(original, None)
    assert result == original


def test_normalize_stream_chunk_with_valid_edit(callback):
    """Test normalization when edit is ModelResponseStream."""
    original = MagicMock()
    edited = ModelResponseStream(
        id="test",
        choices=[{"index": 0, "delta": {"content": "edited"}}],
        created=1234567890,
        model="gpt-3.5-turbo",
        object="chat.completion.chunk"
    )

    result = callback._normalize_stream_chunk(original, edited)
    assert result == edited


def test_normalize_stream_chunk_with_dict_edit(callback):
    """Test normalization when edit is a dictionary."""
    original = MagicMock()
    edited = {
        "id": "test",
        "choices": [{"index": 0, "delta": {"content": "edited"}}],
        "created": 1234567890,
        "model": "gpt-3.5-turbo",
        "object": "chat.completion.chunk",
        "_source_type_": "dict"  # Should be stripped
    }

    result = callback._normalize_stream_chunk(original, edited)
    assert isinstance(result, ModelResponseStream)
    assert result.choices[0]["delta"]["content"] == "edited"


def test_normalize_stream_chunk_with_empty_dict(callback):
    """Test normalization fails gracefully with empty dict."""
    original = ModelResponseStream(
        id="test",
        choices=[{"index": 0, "delta": {"content": "original"}}],
        created=1234567890,
        model="gpt-3.5-turbo",
        object="chat.completion.chunk"
    )
    edited = {}

    with patch("config.litellm_callback.verbose_logger") as mock_logger:
        result = callback._normalize_stream_chunk(original, edited)
        # Should fall back to original on error
        assert result == original
        mock_logger.warning.assert_called()


def test_normalize_stream_chunk_with_partial_dict(callback):
    """Test normalization with incomplete dictionary."""
    original = ModelResponseStream(
        id="test",
        choices=[{"index": 0, "delta": {"content": "original"}}],
        created=1234567890,
        model="gpt-3.5-turbo",
        object="chat.completion.chunk"
    )
    edited = {
        "id": "test",
        # Missing required fields
    }

    with patch("config.litellm_callback.verbose_logger") as mock_logger:
        result = callback._normalize_stream_chunk(original, edited)
        # Should fall back to original on error
        assert result == original
        mock_logger.warning.assert_called()


def test_normalize_stream_chunk_with_invalid_type(callback):
    """Test normalization raises error for unexpected types."""
    original = MagicMock()
    edited = ["invalid", "type"]

    with pytest.raises(TypeError, match="unexpected policy stream result type"):
        callback._normalize_stream_chunk(original, edited)


def test_normalize_stream_chunk_with_invalid_original(callback):
    """Test normalization raises error when original is invalid and no edit."""
    original = {"not": "a ModelResponseStream"}

    with pytest.raises(TypeError, match="expected ModelResponseStream"):
        callback._normalize_stream_chunk(original, None)


def test_update_cumulative_choices():
    """Test the _update_cumulative_choices helper method."""
    cumulative_choices = []
    cumulative_tokens = []
    new_tokens = []
    response = {
        "choices": [
            {"index": 0, "delta": {"content": "Hello"}},
            {"index": 1, "delta": {"content": "World"}}
        ]
    }

    LuthienCallback._update_cumulative_choices(
        cumulative_choices,
        cumulative_tokens,
        new_tokens,
        response
    )

    assert len(cumulative_choices) == 2
    assert len(cumulative_tokens) == 2
    assert cumulative_tokens[0] == ["Hello"]
    assert cumulative_tokens[1] == ["World"]
    assert new_tokens == ["Hello", "World"]


def test_update_cumulative_choices_missing_index():
    """Test that missing index in choice raises ValueError."""
    cumulative_choices = []
    cumulative_tokens = []
    new_tokens = []
    response = {
        "choices": [
            {"delta": {"content": "Hello"}}  # Missing index
        ]
    }

    with pytest.raises(ValueError, match="choice missing index"):
        LuthienCallback._update_cumulative_choices(
            cumulative_choices,
            cumulative_tokens,
            new_tokens,
            response
        )