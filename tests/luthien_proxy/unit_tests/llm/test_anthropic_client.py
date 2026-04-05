"""Unit tests for AnthropicClient."""

from enum import Enum
from unittest.mock import AsyncMock, MagicMock, patch

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
    ThinkingBlock,
    Usage,
)
from anthropic.types.raw_message_delta_event import Delta
from tests.constants import DEFAULT_TEST_MODEL

from luthien_proxy.llm.anthropic_client import AnthropicClient, serialize_no_extras
from luthien_proxy.llm.types.anthropic import AnthropicRequest


@pytest.fixture
def sample_request() -> AnthropicRequest:
    """Create a sample Anthropic request."""
    return AnthropicRequest(
        model=DEFAULT_TEST_MODEL,
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
        model=DEFAULT_TEST_MODEL,
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
                model=DEFAULT_TEST_MODEL,
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
        client = AnthropicClient(api_key="test-key")
        assert client._client is not None

    def test_init_with_auth_token(self):
        client = AnthropicClient(auth_token="oauth-token")
        assert client._client is not None

    def test_init_with_base_url(self):
        client = AnthropicClient(api_key="test-key", base_url="https://custom.api.com")
        assert client._client.base_url == "https://custom.api.com"

    def test_auth_token_does_not_set_default_headers(self):
        """Auth token construction should not set default_headers."""
        with patch("anthropic.AsyncAnthropic") as MockAnthropic:
            AnthropicClient(auth_token="some-token")
            call_kwargs = MockAnthropic.call_args.kwargs
            # default_headers should not be set for auth_token clients
            assert "default_headers" not in call_kwargs or call_kwargs["default_headers"] is None

    def test_no_credentials_raises_value_error(self):
        """Constructing without api_key or auth_token raises ValueError."""
        with pytest.raises(ValueError, match="Either api_key or auth_token must be provided"):
            AnthropicClient()

    def test_auth_token_clears_api_key(self):
        """When using auth_token, _client.api_key must be None to prevent SDK env-read."""
        client = AnthropicClient(auth_token="oauth-token")
        assert client._client.api_key is None

    def test_api_key_preserved_when_not_using_auth_token(self):
        """When using api_key, _client.api_key should be preserved, not None."""
        client = AnthropicClient(api_key="test-api-key")
        assert client._client.api_key is not None
        assert client._client.api_key == "test-api-key"


def _mock_stream_for_message(mock_async_client: AsyncMock, message: Message) -> None:
    """Set up mock_async_client.messages.stream to return a context manager
    whose get_final_message() resolves to the given Message.

    complete() now uses messages.stream() internally (some models like Opus
    require streaming), so all complete() tests need this mock pattern.
    """
    mock_stream = MagicMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aexit__ = AsyncMock(return_value=None)
    mock_stream.get_final_message = AsyncMock(return_value=message)
    mock_async_client.messages.stream = MagicMock(return_value=mock_stream)


class TestAnthropicClientComplete:
    """Test AnthropicClient.complete() method."""

    @pytest.mark.asyncio
    async def test_complete_success(self, sample_request: AnthropicRequest, sample_message: Message):
        """Test successful non-streaming completion."""
        client = AnthropicClient(api_key="test-key")

        mock_async_client = AsyncMock()
        _mock_stream_for_message(mock_async_client, sample_message)
        client._client = mock_async_client

        result = await client.complete(sample_request)

        assert result["id"] == "msg_123"
        assert result["type"] == "message"
        assert result["role"] == "assistant"
        assert result["model"] == DEFAULT_TEST_MODEL
        assert result["stop_reason"] == "end_turn"
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        assert result["content"][0]["text"] == "Hi there!"

        mock_async_client.messages.stream.assert_called_once()
        call_kwargs = mock_async_client.messages.stream.call_args.kwargs
        assert call_kwargs["model"] == DEFAULT_TEST_MODEL
        assert call_kwargs["max_tokens"] == 100
        assert len(call_kwargs["messages"]) == 1

    @pytest.mark.asyncio
    async def test_complete_with_system_prompt(self, sample_message: Message):
        """Test completion with system prompt."""
        client = AnthropicClient(api_key="test-key")
        request = AnthropicRequest(
            model=DEFAULT_TEST_MODEL,
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
            system="You are a helpful assistant.",
        )

        mock_async_client = AsyncMock()
        _mock_stream_for_message(mock_async_client, sample_message)
        client._client = mock_async_client

        await client.complete(request)

        call_kwargs = mock_async_client.messages.stream.call_args.kwargs
        assert call_kwargs["system"] == "You are a helpful assistant."

    @pytest.mark.asyncio
    async def test_complete_with_optional_params(self, sample_message: Message):
        """Test completion with optional parameters."""
        client = AnthropicClient(api_key="test-key")
        request = AnthropicRequest(
            model=DEFAULT_TEST_MODEL,
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
            temperature=0.7,
            top_p=0.9,
            top_k=40,
            stop_sequences=["END"],
        )

        mock_async_client = AsyncMock()
        _mock_stream_for_message(mock_async_client, sample_message)
        client._client = mock_async_client

        await client.complete(request)

        call_kwargs = mock_async_client.messages.stream.call_args.kwargs
        assert call_kwargs["temperature"] == 0.7
        assert call_kwargs["top_p"] == 0.9
        assert call_kwargs["top_k"] == 40
        assert call_kwargs["stop_sequences"] == ["END"]

    @pytest.mark.asyncio
    async def test_complete_excludes_none_values(self, sample_message: Message):
        """Test that complete excludes None/unset values from request."""
        client = AnthropicClient(api_key="test-key")
        request = AnthropicRequest(
            model=DEFAULT_TEST_MODEL,
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
            # temperature not set - should not be in call
        )

        mock_async_client = AsyncMock()
        _mock_stream_for_message(mock_async_client, sample_message)
        client._client = mock_async_client

        await client.complete(request)

        call_kwargs = mock_async_client.messages.stream.call_args.kwargs
        assert "temperature" not in call_kwargs

    @pytest.mark.asyncio
    async def test_complete_with_thinking(self, sample_message: Message):
        """Test completion with thinking parameter."""
        client = AnthropicClient(api_key="test-key")
        request = AnthropicRequest(
            model=DEFAULT_TEST_MODEL,
            messages=[{"role": "user", "content": "Think about this"}],
            max_tokens=16000,
            thinking={"type": "enabled", "budget_tokens": 10000},
        )

        mock_async_client = AsyncMock()
        _mock_stream_for_message(mock_async_client, sample_message)
        client._client = mock_async_client

        await client.complete(request)

        call_kwargs = mock_async_client.messages.stream.call_args.kwargs
        assert call_kwargs["thinking"] == {"type": "enabled", "budget_tokens": 10000}

    @pytest.mark.asyncio
    async def test_complete_forwards_cache_tokens_when_present(self, sample_request: AnthropicRequest):
        client = AnthropicClient(api_key="test-key")
        message_with_cache = Message(
            id="msg_cache",
            type="message",
            role="assistant",
            content=[TextBlock(type="text", text="cached")],
            model=DEFAULT_TEST_MODEL,
            stop_reason="end_turn",
            usage=Usage(
                input_tokens=10,
                output_tokens=5,
                cache_creation_input_tokens=100,
                cache_read_input_tokens=50,
            ),
        )
        mock_async_client = AsyncMock()
        _mock_stream_for_message(mock_async_client, message_with_cache)
        client._client = mock_async_client

        result = await client.complete(sample_request)

        assert result["usage"]["cache_creation_input_tokens"] == 100
        assert result["usage"]["cache_read_input_tokens"] == 50

    @pytest.mark.asyncio
    async def test_complete_omits_cache_tokens_when_absent(
        self, sample_request: AnthropicRequest, sample_message: Message
    ):
        client = AnthropicClient(api_key="test-key")
        mock_async_client = AsyncMock()
        _mock_stream_for_message(mock_async_client, sample_message)
        client._client = mock_async_client

        result = await client.complete(sample_request)

        assert "cache_creation_input_tokens" not in result["usage"]
        assert "cache_read_input_tokens" not in result["usage"]


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
            model=DEFAULT_TEST_MODEL,
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
        assert call_kwargs["model"] == DEFAULT_TEST_MODEL
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

    @pytest.mark.asyncio
    async def test_complete_forwards_unknown_fields_via_extra_body(self, sample_message: Message):
        """Test that unknown request fields are forwarded via extra_body."""
        client = AnthropicClient(api_key="test-key")
        request: dict = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
            "output_config": {"format": {"type": "json"}},
            "service_tier": "auto",
        }

        mock_async_client = AsyncMock()
        _mock_stream_for_message(mock_async_client, sample_message)
        client._client = mock_async_client

        await client.complete(request)

        call_kwargs = mock_async_client.messages.stream.call_args.kwargs
        assert "extra_body" in call_kwargs
        assert call_kwargs["extra_body"]["output_config"] == {"format": {"type": "json"}}
        assert call_kwargs["extra_body"]["service_tier"] == "auto"

    @pytest.mark.asyncio
    async def test_complete_no_extra_body_when_all_fields_known(self, sample_message: Message):
        """Test that extra_body is not set when request only has known fields."""
        client = AnthropicClient(api_key="test-key")
        request = AnthropicRequest(
            model=DEFAULT_TEST_MODEL,
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
        )

        mock_async_client = AsyncMock()
        _mock_stream_for_message(mock_async_client, sample_message)
        client._client = mock_async_client

        await client.complete(request)

        call_kwargs = mock_async_client.messages.stream.call_args.kwargs
        assert "extra_body" not in call_kwargs


class TestSerializeNoExtras:
    def test_strips_pydantic_extra_fields(self):
        block = TextBlock.model_validate({"type": "text", "text": "hello", "snapshot": "hello"})
        result = serialize_no_extras(block)
        assert "snapshot" not in result
        assert result["type"] == "text"
        assert result["text"] == "hello"

    def test_preserves_known_fields(self):
        block = TextBlock(type="text", text="hi")
        result = serialize_no_extras(block)
        assert result["type"] == "text"
        assert result["text"] == "hi"

    def test_strips_nested_extras(self):
        block = TextBlock.model_validate({"type": "text", "text": "x", "snapshot": "x"})
        event = RawContentBlockStartEvent(type="content_block_start", index=0, content_block=block)
        result = serialize_no_extras(event)
        assert "snapshot" not in result["content_block"]
        assert result["content_block"]["type"] == "text"

    def test_plain_values_pass_through(self):
        assert serialize_no_extras("string") == "string"
        assert serialize_no_extras(42) == 42
        assert serialize_no_extras(None) is None

    def test_list_of_models(self):
        blocks = [
            TextBlock.model_validate({"type": "text", "text": "a", "snapshot": "a"}),
            TextBlock.model_validate({"type": "text", "text": "b", "snapshot": "b"}),
        ]
        results = serialize_no_extras(blocks)
        assert all("snapshot" not in r for r in results)
        assert [r["text"] for r in results] == ["a", "b"]

    def test_enum_values_are_unwrapped(self):
        class Color(Enum):
            RED = "red"

        assert serialize_no_extras(Color.RED) == "red"

    def test_dict_passes_through(self):
        assert serialize_no_extras({"a": 1, "b": "x"}) == {"a": 1, "b": "x"}

    def test_tuple_elements_are_recursed(self):
        block = TextBlock.model_validate({"type": "text", "text": "hi", "snapshot": "hi"})
        result = serialize_no_extras((block,))
        assert len(result) == 1
        assert result[0]["text"] == "hi"
        assert "snapshot" not in result[0]

    def test_strips_extras_from_thinking_block(self):
        block = ThinkingBlock.model_validate(
            {"type": "thinking", "thinking": "some reasoning", "signature": "sig123", "snapshot": "snap"}
        )
        result = serialize_no_extras(block)
        assert "snapshot" not in result
        assert result["type"] == "thinking"
        assert result["thinking"] == "some reasoning"
        assert result["signature"] == "sig123"
