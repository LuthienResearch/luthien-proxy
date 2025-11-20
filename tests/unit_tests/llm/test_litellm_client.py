"""Unit tests for LiteLLMClient."""

from unittest.mock import AsyncMock, patch

import pytest
from litellm.types.utils import ModelResponse

from luthien_proxy.llm.litellm_client import LiteLLMClient, _normalize_model_name
from luthien_proxy.messages import Request


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
async def test_complete_success(sample_request, sample_response):
    """Test successful non-streaming completion."""
    client = LiteLLMClient()

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_completion:
        mock_completion.return_value = sample_response

        result = await client.complete(sample_request)

        assert result == sample_response
        mock_completion.assert_called_once()
        call_kwargs = mock_completion.call_args.kwargs
        assert call_kwargs["model"] == "openai/gpt-4"  # Normalized with openai/ prefix
        assert call_kwargs["stream"] is False
        assert len(call_kwargs["messages"]) == 1


@pytest.mark.asyncio
async def test_stream_success(sample_request, sample_chunks):
    """Test successful streaming."""
    client = LiteLLMClient()

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
async def test_complete_excludes_none_values(sample_request, sample_response):
    """Test that complete excludes None values from request."""
    client = LiteLLMClient()
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
async def test_stream_excludes_none_values(sample_request, sample_chunks):
    """Test that stream excludes None values from request."""
    client = LiteLLMClient()
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


class TestNormalizeModelName:
    """Tests for _normalize_model_name function."""

    def test_gpt_models_get_openai_prefix(self):
        """Test that GPT models get openai/ prefix."""
        assert _normalize_model_name("gpt-4") == "openai/gpt-4"
        assert _normalize_model_name("gpt-3.5-turbo") == "openai/gpt-3.5-turbo"
        assert _normalize_model_name("gpt-5.1-codex-max") == "openai/gpt-5.1-codex-max"

    def test_o1_o3_models_get_openai_prefix(self):
        """Test that o1/o3 models get openai/ prefix."""
        assert _normalize_model_name("o1-preview") == "openai/o1-preview"
        assert _normalize_model_name("o1-mini") == "openai/o1-mini"
        assert _normalize_model_name("o3-mini") == "openai/o3-mini"

    def test_legacy_models_get_openai_prefix(self):
        """Test that legacy OpenAI models get openai/ prefix."""
        assert _normalize_model_name("davinci-002") == "openai/davinci-002"
        assert _normalize_model_name("babbage-002") == "openai/babbage-002"

    def test_already_prefixed_models_unchanged(self):
        """Test that models with provider prefix are unchanged."""
        assert _normalize_model_name("openai/gpt-4") == "openai/gpt-4"
        assert _normalize_model_name("anthropic/claude-3-opus") == "anthropic/claude-3-opus"
        assert _normalize_model_name("ollama/llama2") == "ollama/llama2"

    def test_non_openai_models_unchanged(self):
        """Test that non-OpenAI models without prefix are unchanged."""
        assert _normalize_model_name("claude-3-opus") == "claude-3-opus"
        assert _normalize_model_name("llama2") == "llama2"
        assert _normalize_model_name("gemma2:2b") == "gemma2:2b"
