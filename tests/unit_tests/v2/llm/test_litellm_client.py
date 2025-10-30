"""Unit tests for LiteLLMClient."""

from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import pytest
from litellm.types.utils import ModelResponse

from luthien_proxy.v2.llm.litellm_client import LiteLLMClient
from luthien_proxy.v2.messages import Request


@pytest.fixture
def mock_session():
    """Create a mock aiohttp.ClientSession."""
    return Mock(spec=aiohttp.ClientSession)


@pytest.fixture
def sample_request():
    """Create a sample request."""
    return Request(
        model="gpt-4",
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=100,
    )


@pytest.fixture
def sample_response():
    """Create a sample ModelResponse."""
    return ModelResponse(
        id="test-id",
        object="chat.completion",
        created=1234567890,
        model="gpt-4",
        choices=[
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hi there!"},
                "finish_reason": "stop",
            }
        ],
    )


@pytest.fixture
def sample_chunks():
    """Create sample streaming chunks."""
    return [
        ModelResponse(
            id="test-id",
            object="chat.completion.chunk",
            created=1234567890,
            model="gpt-4",
            choices=[
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": "Hi"},
                    "finish_reason": None,
                }
            ],
        ),
        ModelResponse(
            id="test-id",
            object="chat.completion.chunk",
            created=1234567890,
            model="gpt-4",
            choices=[
                {
                    "index": 0,
                    "delta": {"content": " there!"},
                    "finish_reason": None,
                }
            ],
        ),
        ModelResponse(
            id="test-id",
            object="chat.completion.chunk",
            created=1234567890,
            model="gpt-4",
            choices=[
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
        ),
    ]


@pytest.mark.asyncio
async def test_complete_success(mock_session, sample_request, sample_response):
    """Test successful non-streaming completion."""
    client = LiteLLMClient(shared_session=mock_session)

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_completion:
        mock_completion.return_value = sample_response

        result = await client.complete(sample_request)

        assert result == sample_response
        mock_completion.assert_called_once()
        call_kwargs = mock_completion.call_args.kwargs
        assert call_kwargs["model"] == "gpt-4"
        assert call_kwargs["stream"] is False
        assert len(call_kwargs["messages"]) == 1


@pytest.mark.asyncio
async def test_stream_success(mock_session, sample_request, sample_chunks):
    """Test successful streaming."""
    client = LiteLLMClient(shared_session=mock_session)

    async def mock_stream():
        for chunk in sample_chunks:
            yield chunk

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_completion:
        mock_completion.return_value = mock_stream()

        chunks = []
        stream_iter = await client.stream(sample_request)
        async for chunk in stream_iter:
            chunks.append(chunk)

        assert len(chunks) == 3
        assert chunks[0].choices[0]["delta"]["content"] == "Hi"
        assert chunks[1].choices[0]["delta"]["content"] == " there!"
        assert chunks[2].choices[0]["finish_reason"] == "stop"

        mock_completion.assert_called_once()
        call_kwargs = mock_completion.call_args.kwargs
        assert call_kwargs["stream"] is True


@pytest.mark.asyncio
async def test_complete_excludes_none_values(mock_session, sample_request, sample_response):
    """Test that complete excludes None values from request."""
    client = LiteLLMClient(shared_session=mock_session)
    request_with_none = Request(
        model="gpt-4",
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=None,  # Should be excluded
        temperature=0.7,
    )

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_completion:
        mock_completion.return_value = sample_response
        await client.complete(request_with_none)

        call_kwargs = mock_completion.call_args.kwargs
        assert "max_tokens" not in call_kwargs
        assert call_kwargs["temperature"] == 0.7


@pytest.mark.asyncio
async def test_stream_excludes_none_values(mock_session, sample_request, sample_chunks):
    """Test that stream excludes None values from request."""
    client = LiteLLMClient(shared_session=mock_session)
    request_with_none = Request(
        model="gpt-4",
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=None,  # Should be excluded
        temperature=0.7,
    )

    async def mock_stream():
        for chunk in sample_chunks:
            yield chunk

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_completion:
        mock_completion.return_value = mock_stream()

        stream_iter = await client.stream(request_with_none)
        async for _ in stream_iter:
            pass

        call_kwargs = mock_completion.call_args.kwargs
        assert "max_tokens" not in call_kwargs
        assert call_kwargs["temperature"] == 0.7


def test_requires_session():
    """Test that LiteLLMClient requires a valid aiohttp.ClientSession."""
    with pytest.raises(TypeError):
        LiteLLMClient(shared_session=None)

    with pytest.raises(TypeError):
        LiteLLMClient(shared_session="not a session")

    with pytest.raises(TypeError):
        LiteLLMClient(shared_session=42)
