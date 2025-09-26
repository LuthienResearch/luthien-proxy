"""Test suite for LiteLLM callback error handling and type normalization."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from config.litellm_callback import LuthienCallback
from litellm.types.utils import ModelResponseStream


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
            "Internal Server Error", request=MagicMock(), response=mock_response
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
            "Bad Request", request=MagicMock(), response=mock_response
        )
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        result = await callback._apost_hook("test_hook", {"data": "test"})

        assert result is None


@pytest.mark.asyncio
async def test_apost_hook_raises_on_non_json_response(callback):
    """Ensure the hook raises when the control plane response is not JSON."""
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.content = b"ok"
        mock_response.raise_for_status.return_value = None
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        with pytest.raises(httpx.HTTPError, match="Unexpected content-type"):
            await callback._apost_hook("test_hook", {"data": "test"})


@pytest.mark.asyncio
async def test_apost_hook_raises_on_empty_response(callback):
    """Ensure the hook raises when the control plane returns empty JSON body."""
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.content = b""
        mock_response.raise_for_status.return_value = None
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        with pytest.raises(httpx.HTTPError, match="Empty response"):
            await callback._apost_hook("test_hook", {"data": "test"})


@pytest.mark.asyncio
async def test_apost_hook_raises_on_invalid_json(callback):
    """Ensure the hook raises when the control plane returns malformed JSON."""
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.content = b"not-json"
        mock_response.raise_for_status.return_value = None
        mock_response.json.side_effect = ValueError("bad json")
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        with pytest.raises(httpx.HTTPError, match="Invalid JSON"):
            await callback._apost_hook("test_hook", {"data": "test"})


def test_normalize_stream_chunk_with_model_response_edit(callback):
    """Fail when policy returns a ModelResponseStream instead of JSON."""
    edited = ModelResponseStream.model_validate(
        {
            "id": "test",
            "choices": [{"index": 0, "delta": {"content": "edited"}}],
            "created": 1234567890,
            "model": "gpt-3.5-turbo",
            "object": "chat.completion.chunk",
        }
    )

    with pytest.raises(TypeError, match="policy stream chunks must be dict"):
        callback._normalize_stream_chunk(edited)


def test_normalize_stream_chunk_with_dict_edit(callback):
    """Test normalization when edit is a dictionary."""
    edited = {
        "id": "test",
        "choices": [{"index": 0, "delta": {"content": "edited"}}],
        "created": 1234567890,
        "model": "gpt-3.5-turbo",
        "object": "chat.completion.chunk",
        "_source_type_": "dict",  # Should be stripped
    }

    result = callback._normalize_stream_chunk(edited)
    assert isinstance(result, ModelResponseStream)
    assert result.choices[0]["delta"]["content"] == "edited"


def test_normalize_stream_chunk_with_empty_dict(callback):
    """Test normalization fails gracefully with empty dict."""
    edited = {}

    with pytest.raises(ValueError, match="policy returned empty stream chunk"):
        callback._normalize_stream_chunk(edited)


def test_normalize_stream_chunk_with_partial_dict(callback):
    """Test normalization with incomplete dictionary."""
    edited = {
        "id": "test",
        # Missing required fields
    }

    with pytest.raises(ValueError, match="missing required fields"):
        callback._normalize_stream_chunk(edited)


def test_normalize_stream_chunk_with_invalid_type(callback):
    """Test normalization raises error for unexpected types."""
    edited = ["invalid", "type"]

    with pytest.raises(TypeError, match="policy stream chunks must be dict"):
        callback._normalize_stream_chunk(edited)


def test_normalize_stream_chunk_none(callback):
    """Policy must not return None for stream chunks."""
    with pytest.raises(ValueError, match="policy returned no stream chunk"):
        callback._normalize_stream_chunk(None)


def test_update_cumulative_choices():
    """Test the _update_cumulative_choices helper method."""
    cumulative_choices = []
    cumulative_tokens = []
    new_tokens = []
    response = {"choices": [{"index": 0, "delta": {"content": "Hello"}}, {"index": 1, "delta": {"content": "World"}}]}

    LuthienCallback._update_cumulative_choices(cumulative_choices, cumulative_tokens, new_tokens, response)

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
        LuthienCallback._update_cumulative_choices(cumulative_choices, cumulative_tokens, new_tokens, response)
