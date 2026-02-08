"""Unit tests for AnthropicClient."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from anthropic.types import (
    Message,
    MessageDeltaUsage,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    RawMessageStartEvent,
    RawMessageStopEvent,
    TextBlock,
    TextDelta,
    Usage,
)
from anthropic.types.raw_message_delta_event import Delta

from luthien_proxy.llm.anthropic_client import AnthropicClient
from luthien_proxy.llm.types.anthropic import AnthropicRequest


@pytest.fixture
def sample_request() -> AnthropicRequest:
    """Create a sample Anthropic request."""
    return AnthropicRequest(
        model="claude-sonnet-4-20250514",
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=100,
    )


@pytest.fixture
def sample_message() -> Message:
    """Create a sample Anthropic Message response."""
    return Message(
        id="msg_123",
        type="message",
        role="assistant",
        content=[TextBlock(type="text", text="Hi there!")],
        model="claude-sonnet-4-20250514",
        stop_reason="end_turn",
        usage=Usage(input_tokens=10, output_tokens=5),
    )


@pytest.fixture
def sample_stream_events() -> list:
    """Create sample streaming events."""
    return [
        RawMessageStartEvent(
            type="message_start",
            message=Message(
                id="msg_123",
                type="message",
                role="assistant",
                content=[],
                model="claude-sonnet-4-20250514",
                stop_reason=None,
                usage=Usage(input_tokens=10, output_tokens=0),
            ),
        ),
        RawContentBlockStartEvent(
            type="content_block_start",
            index=0,
            content_block=TextBlock(type="text", text=""),
        ),
        RawContentBlockDeltaEvent(
            type="content_block_delta",
            index=0,
            delta=TextDelta(type="text_delta", text="Hi"),
        ),
        RawContentBlockDeltaEvent(
            type="content_block_delta",
            index=0,
            delta=TextDelta(type="text_delta", text=" there!"),
        ),
        RawContentBlockStopEvent(
            type="content_block_stop",
            index=0,
        ),
        RawMessageDeltaEvent(
            type="message_delta",
            delta=Delta(stop_reason="end_turn", stop_sequence=None),
            usage=MessageDeltaUsage(output_tokens=5),
        ),
        RawMessageStopEvent(type="message_stop"),
    ]


class TestAnthropicClientInit:
    """Test AnthropicClient initialization."""

    def test_init_with_api_key(self):
        """Test client initialization with API key."""
        client = AnthropicClient(api_key="test-key")
        assert client._client is not None

    def test_init_with_base_url(self):
        """Test client initialization with custom base URL."""
        client = AnthropicClient(api_key="test-key", base_url="https://custom.api.com")
        assert client._client.base_url == "https://custom.api.com"

    def test_init_creates_client_immediately(self):
        """Test that client is created during initialization (not lazily)."""
        client = AnthropicClient(api_key="test-key")
        assert client._client is not None


class TestAnthropicClientComplete:
    """Test AnthropicClient.complete() method."""

    @pytest.mark.asyncio
    async def test_complete_success(self, sample_request: AnthropicRequest, sample_message: Message):
        """Test successful non-streaming completion."""
        client = AnthropicClient(api_key="test-key")

        mock_async_client = AsyncMock()
        mock_async_client.messages.create = AsyncMock(return_value=sample_message)
        client._client = mock_async_client

        result = await client.complete(sample_request)

        assert result["id"] == "msg_123"
        assert result["type"] == "message"
        assert result["role"] == "assistant"
        assert result["model"] == "claude-sonnet-4-20250514"
        assert result["stop_reason"] == "end_turn"
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        assert result["content"][0]["text"] == "Hi there!"

        mock_async_client.messages.create.assert_called_once()
        call_kwargs = mock_async_client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-sonnet-4-20250514"
        assert call_kwargs["max_tokens"] == 100
        assert len(call_kwargs["messages"]) == 1

    @pytest.mark.asyncio
    async def test_complete_with_system_prompt(self, sample_message: Message):
        """Test completion with system prompt."""
        client = AnthropicClient(api_key="test-key")
        request = AnthropicRequest(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
            system="You are a helpful assistant.",
        )

        mock_async_client = AsyncMock()
        mock_async_client.messages.create = AsyncMock(return_value=sample_message)
        client._client = mock_async_client

        await client.complete(request)

        call_kwargs = mock_async_client.messages.create.call_args.kwargs
        assert call_kwargs["system"] == "You are a helpful assistant."

    @pytest.mark.asyncio
    async def test_complete_with_optional_params(self, sample_message: Message):
        """Test completion with optional parameters."""
        client = AnthropicClient(api_key="test-key")
        request = AnthropicRequest(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
            temperature=0.7,
            top_p=0.9,
            top_k=40,
            stop_sequences=["END"],
        )

        mock_async_client = AsyncMock()
        mock_async_client.messages.create = AsyncMock(return_value=sample_message)
        client._client = mock_async_client

        await client.complete(request)

        call_kwargs = mock_async_client.messages.create.call_args.kwargs
        assert call_kwargs["temperature"] == 0.7
        assert call_kwargs["top_p"] == 0.9
        assert call_kwargs["top_k"] == 40
        assert call_kwargs["stop_sequences"] == ["END"]

    @pytest.mark.asyncio
    async def test_complete_excludes_none_values(self, sample_message: Message):
        """Test that complete excludes None/unset values from request."""
        client = AnthropicClient(api_key="test-key")
        request = AnthropicRequest(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
            # temperature not set - should not be in call
        )

        mock_async_client = AsyncMock()
        mock_async_client.messages.create = AsyncMock(return_value=sample_message)
        client._client = mock_async_client

        await client.complete(request)

        call_kwargs = mock_async_client.messages.create.call_args.kwargs
        assert "temperature" not in call_kwargs

    @pytest.mark.asyncio
    async def test_complete_with_thinking(self, sample_message: Message):
        """Test completion with thinking parameter."""
        client = AnthropicClient(api_key="test-key")
        request = AnthropicRequest(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "Think about this"}],
            max_tokens=16000,
            thinking={"type": "enabled", "budget_tokens": 10000},
        )

        mock_async_client = AsyncMock()
        mock_async_client.messages.create = AsyncMock(return_value=sample_message)
        client._client = mock_async_client

        await client.complete(request)

        call_kwargs = mock_async_client.messages.create.call_args.kwargs
        assert call_kwargs["thinking"] == {"type": "enabled", "budget_tokens": 10000}


class TestAnthropicClientStream:
    """Test AnthropicClient.stream() method."""

    @pytest.mark.asyncio
    async def test_stream_success(self, sample_request: AnthropicRequest, sample_stream_events: list):
        """Test successful streaming."""
        client = AnthropicClient(api_key="test-key")

        async def mock_stream_iter():
            for event in sample_stream_events:
                yield event

        mock_async_client = AsyncMock()
        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda self: mock_stream_iter()
        mock_async_client.messages.stream = MagicMock(return_value=mock_stream)
        client._client = mock_async_client

        events = []
        async for event in client.stream(sample_request):
            events.append(event)

        assert len(events) == 7
        assert events[0].type == "message_start"
        assert events[1].type == "content_block_start"
        assert events[2].type == "content_block_delta"
        assert events[3].type == "content_block_delta"
        assert events[4].type == "content_block_stop"
        assert events[5].type == "message_delta"
        assert events[6].type == "message_stop"

    @pytest.mark.asyncio
    async def test_stream_passes_parameters(self, sample_stream_events: list):
        """Test that stream passes parameters correctly."""
        client = AnthropicClient(api_key="test-key")
        request = AnthropicRequest(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
            temperature=0.5,
            system="Be concise.",
        )

        async def mock_stream_iter():
            for event in sample_stream_events:
                yield event

        mock_async_client = AsyncMock()
        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda self: mock_stream_iter()
        mock_async_client.messages.stream = MagicMock(return_value=mock_stream)
        client._client = mock_async_client

        async for _ in client.stream(request):
            pass

        mock_async_client.messages.stream.assert_called_once()
        call_kwargs = mock_async_client.messages.stream.call_args.kwargs
        assert call_kwargs["model"] == "claude-sonnet-4-20250514"
        assert call_kwargs["max_tokens"] == 100
        assert call_kwargs["temperature"] == 0.5
        assert call_kwargs["system"] == "Be concise."

    @pytest.mark.asyncio
    async def test_stream_text_delta_content(self, sample_request: AnthropicRequest, sample_stream_events: list):
        """Test that text delta events contain expected content."""
        client = AnthropicClient(api_key="test-key")

        async def mock_stream_iter():
            for event in sample_stream_events:
                yield event

        mock_async_client = AsyncMock()
        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.__aiter__ = lambda self: mock_stream_iter()
        mock_async_client.messages.stream = MagicMock(return_value=mock_stream)
        client._client = mock_async_client

        text_deltas = []
        async for event in client.stream(sample_request):
            if event.type == "content_block_delta":
                text_deltas.append(event.delta.text)

        assert text_deltas == ["Hi", " there!"]


class TestPrepareRequestKwargs:
    """Test _prepare_request_kwargs sanitization."""

    def test_sanitizes_cache_control_extra_fields_on_tools(self):
        """Tools with cache_control containing extra fields should be sanitized.

        Claude Code sends cache_control: {"type": "ephemeral", "scope": "turn"}
        but Anthropic API only accepts cache_control: {"type": "ephemeral"}.
        The proxy should strip unsupported fields to prevent 400 errors.
        """
        client = AnthropicClient(api_key="test-key")
        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
            "tools": [
                {
                    "name": "read_file",
                    "description": "Read a file",
                    "input_schema": {"type": "object", "properties": {}},
                    "cache_control": {"type": "ephemeral", "scope": "turn"},
                },
                {
                    "name": "write_file",
                    "description": "Write a file",
                    "input_schema": {"type": "object", "properties": {}},
                },
            ],
        }

        kwargs = client._prepare_request_kwargs(request)

        # cache_control should only have "type", "scope" should be stripped
        assert kwargs["tools"][0]["cache_control"] == {"type": "ephemeral"}
        # Tool without cache_control should be unchanged
        assert "cache_control" not in kwargs["tools"][1]

    def test_preserves_valid_cache_control_on_tools(self):
        """Tools with valid cache_control (no extra fields) should be unchanged."""
        client = AnthropicClient(api_key="test-key")
        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
            "tools": [
                {
                    "name": "read_file",
                    "description": "Read a file",
                    "input_schema": {"type": "object", "properties": {}},
                    "cache_control": {"type": "ephemeral"},
                },
            ],
        }

        kwargs = client._prepare_request_kwargs(request)

        assert kwargs["tools"][0]["cache_control"] == {"type": "ephemeral"}

    def test_sanitizes_cache_control_on_system_blocks(self):
        """System blocks with cache_control containing extra fields should be sanitized."""
        client = AnthropicClient(api_key="test-key")
        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
            "system": [
                {
                    "type": "text",
                    "text": "You are helpful",
                    "cache_control": {"type": "ephemeral", "scope": "session"},
                },
            ],
        }

        kwargs = client._prepare_request_kwargs(request)

        assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
