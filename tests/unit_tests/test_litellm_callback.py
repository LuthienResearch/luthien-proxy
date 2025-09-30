"""Test suite for LiteLLM callback error handling and type normalization."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from config.litellm_callback import LuthienCallback  # noqa: E402
from litellm.types.utils import ModelResponseStream  # noqa: E402


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


@pytest.mark.asyncio
async def test_streaming_hook_without_stream_id_passthrough(callback):
    chunk = ModelResponseStream.model_validate(
        {
            "id": "stream",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "gpt-test",
            "choices": [{"index": 0, "delta": {"content": "hi"}}],
        }
    )

    async def upstream():
        yield chunk

    results = []
    async for item in callback.async_post_call_streaming_iterator_hook(None, upstream(), {}):
        results.append(item)

    assert results == [chunk]


@pytest.mark.asyncio
async def test_streaming_hook_falls_back_on_connection_error(callback):
    chunk = ModelResponseStream.model_validate(
        {
            "id": "stream",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "gpt-test",
            "choices": [{"index": 0, "delta": {"content": "hi"}}],
        }
    )

    async def upstream():
        yield chunk

    manager = AsyncMock()
    manager.get_or_create.side_effect = RuntimeError("boom")

    with patch.object(callback, "_get_connection_manager", return_value=manager):
        results = []
        async for item in callback.async_post_call_streaming_iterator_hook(
            None,
            upstream(),
            {"litellm_call_id": "abc"},
        ):
            results.append(item)

    assert results == [chunk]


@pytest.mark.asyncio
async def test_streaming_hook_returns_transformed_chunk(callback):
    original = ModelResponseStream.model_validate(
        {
            "id": "stream",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "gpt-test",
            "choices": [{"index": 0, "delta": {"content": "hi"}}],
        }
    )

    transformed = {
        "id": "stream",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "gpt-test",
        "choices": [{"index": 0, "delta": {"content": "HELLO"}}],
    }

    class DummyConnection:
        def __init__(self):
            self.sent = []
            self._delivered = False

        async def send(self, message):
            self.sent.append(message)

        async def receive(self, timeout=None):
            if not self._delivered:
                self._delivered = True
                return {"type": "CHUNK", "data": transformed}
            await asyncio.sleep(0)
            return None

    manager = AsyncMock()
    connection = DummyConnection()
    manager.get_or_create.return_value = connection
    manager.lookup.return_value = connection
    manager.close.return_value = None

    with patch.object(callback, "_get_connection_manager", return_value=manager):
        results = []

        async def upstream():
            yield original

        async for item in callback.async_post_call_streaming_iterator_hook(
            None,
            upstream(),
            {"litellm_call_id": "abc"},
        ):
            results.append(item)

    assert [chunk.choices[0]["delta"]["content"] for chunk in results] == ["HELLO"]


class _StreamingConnection:
    def __init__(self, responses):
        self.sent = []
        self._responses = list(responses)

    async def send(self, message):
        self.sent.append(message)

    async def receive(self, timeout=None):
        if not self._responses:
            await asyncio.sleep(0)
            return None
        await asyncio.sleep(0)
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_streaming_hook_cleans_up_after_control_end(callback):
    chunk = ModelResponseStream.model_validate(
        {
            "id": "stream",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "gpt-test",
            "choices": [{"index": 0, "delta": {"content": "hi"}}],
        }
    )

    connection = _StreamingConnection(responses=[{"type": "END"}])

    manager = AsyncMock()
    manager.get_or_create.return_value = connection

    with patch.object(callback, "_get_connection_manager", return_value=manager):
        with patch.object(callback, "_cleanup_stream", new_callable=AsyncMock) as cleanup:

            async def upstream():
                yield chunk

            results = []
            async for item in callback.async_post_call_streaming_iterator_hook(
                None,
                upstream(),
                {"litellm_call_id": "abc"},
            ):
                results.append(item)

    assert results == []
    cleanup.assert_awaited_once_with("abc", send_end=False)


@pytest.mark.asyncio
async def test_streaming_hook_skips_end_when_control_plane_signals_close(callback):
    chunk = ModelResponseStream.model_validate(
        {
            "id": "stream",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "gpt-test",
            "choices": [{"index": 0, "delta": {"content": "hi"}}],
        }
    )

    connection = _StreamingConnection(responses=[{"type": "END"}])

    manager = AsyncMock()
    manager.get_or_create.return_value = connection

    with patch.object(callback, "_get_connection_manager", return_value=manager):
        with patch.object(callback, "_cleanup_stream", new_callable=AsyncMock) as cleanup:

            async def upstream():
                yield chunk

            results = []
            async for item in callback.async_post_call_streaming_iterator_hook(
                None,
                upstream(),
                {"litellm_call_id": "abc"},
            ):
                results.append(item)

    assert results == []
    cleanup.assert_awaited_once_with("abc", send_end=False)


@pytest.mark.asyncio
async def test_streaming_hook_waits_for_control_plane_chunk(callback):
    original = ModelResponseStream.model_validate(
        {
            "id": "stream",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "gpt-test",
            "choices": [{"index": 0, "delta": {"content": "hi"}}],
        }
    )

    transformed = {
        "id": "stream",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "gpt-test",
        "choices": [{"index": 0, "delta": {"content": "HELLO"}}],
    }

    class DelayedConnection:
        def __init__(self):
            self.sent = []
            self._emitted = False

        async def send(self, message):
            self.sent.append(message)

        async def receive(self, timeout=None):
            if self._emitted:
                await asyncio.sleep(0)
                return {"type": "END"}
            await asyncio.sleep(0.01)
            self._emitted = True
            return {"type": "CHUNK", "data": transformed}

    manager = AsyncMock()
    connection = DelayedConnection()
    manager.get_or_create.return_value = connection
    manager.lookup.return_value = connection
    manager.close.return_value = None

    with patch.object(callback, "_get_connection_manager", return_value=manager):
        results = []

        async def upstream():
            yield original

        async for item in callback.async_post_call_streaming_iterator_hook(
            None,
            upstream(),
            {"litellm_call_id": "abc"},
        ):
            results.append(item)

    assert [chunk.choices[0]["delta"]["content"] for chunk in results] == ["HELLO"]


@pytest.mark.asyncio
async def test_success_log_triggers_cleanup(callback):
    with patch.object(callback, "_cleanup_stream", new_callable=AsyncMock) as cleanup:
        await callback.async_log_success_event(
            {"litellm_params": {"metadata": {"litellm_call_id": "abc"}}},
            None,
            None,
            None,
        )
    cleanup.assert_awaited_once_with("abc", send_end=True)


@pytest.mark.asyncio
async def test_failure_log_triggers_cleanup(callback):
    with patch.object(callback, "_cleanup_stream", new_callable=AsyncMock) as cleanup:
        await callback.async_log_failure_event(
            {"litellm_params": {"metadata": {"litellm_call_id": "abc"}}},
            None,
            None,
            None,
        )
    cleanup.assert_awaited_once_with("abc", send_end=False)
