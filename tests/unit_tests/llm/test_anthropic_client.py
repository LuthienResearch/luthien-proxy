"""Unit tests for AnthropicClient."""

from unittest.mock import AsyncMock, MagicMock

import anthropic
import httpx
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

from luthien_proxy.llm.anthropic_client import (
    AnthropicClient,
    _collect_tool_use_ids,
    _deduplicate_tools,
    _is_context_overflow,
    _prune_orphaned_tool_results,
    _sanitize_messages,
    _sanitize_request,
    _try_auto_fix,
)
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


def _make_bad_request_error(message: str) -> anthropic.BadRequestError:
    """Create a BadRequestError with a given message for testing."""
    mock_response = httpx.Response(
        status_code=400,
        json={
            "type": "error",
            "error": {"type": "invalid_request_error", "message": message},
        },
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    )
    return anthropic.BadRequestError(
        message=message,
        response=mock_response,
        body={
            "type": "error",
            "error": {"type": "invalid_request_error", "message": message},
        },
    )


def _make_internal_server_error(
    message: str = "Internal server error",
) -> anthropic.InternalServerError:
    """Create an InternalServerError for testing."""
    mock_response = httpx.Response(
        status_code=500,
        json={"type": "error", "error": {"type": "api_error", "message": message}},
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    )
    return anthropic.InternalServerError(
        message=message,
        response=mock_response,
        body={"type": "error", "error": {"type": "api_error", "message": message}},
    )


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
        assert call_kwargs["thinking"] == {
            "type": "enabled",
            "budget_tokens": 10000,
        }


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


# ---------------------------------------------------------------------------
# Pre-flight sanitization tests
# ---------------------------------------------------------------------------


class TestSanitizeMessages:
    """Test _sanitize_messages function."""

    def test_passthrough_string_content(self):
        """String content should pass through unchanged."""
        messages = [{"role": "user", "content": "Hello"}]
        assert _sanitize_messages(messages) == messages

    def test_passthrough_normal_text_blocks(self):
        """Non-empty text blocks should pass through unchanged."""
        messages = [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Hi there!"}],
            },
        ]
        assert _sanitize_messages(messages) == messages

    def test_strips_empty_text_block(self):
        """Empty text blocks should be removed."""
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": ""},
                    {"type": "tool_use", "id": "t1", "name": "foo", "input": {}},
                ],
            },
        ]
        result = _sanitize_messages(messages)
        assert len(result[0]["content"]) == 1
        assert result[0]["content"][0]["type"] == "tool_use"

    def test_preserves_non_text_blocks(self):
        """Tool use, tool result, and other block types are never filtered."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
                ],
            },
        ]
        assert _sanitize_messages(messages) == messages

    def test_all_empty_text_blocks_keeps_original(self):
        """If ALL blocks are empty text, keep original to avoid empty content list."""
        messages = [
            {"role": "assistant", "content": [{"type": "text", "text": ""}]},
        ]
        result = _sanitize_messages(messages)
        assert result[0]["content"] == [{"type": "text", "text": ""}]

    def test_multiple_messages_mixed(self):
        """Sanitization works across multiple messages."""
        messages = [
            {"role": "user", "content": "Hello"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": ""},
                    {"type": "text", "text": "response"},
                ],
            },
            {"role": "user", "content": [{"type": "text", "text": "follow up"}]},
        ]
        result = _sanitize_messages(messages)
        assert result[0]["content"] == "Hello"
        assert len(result[1]["content"]) == 1
        assert result[1]["content"][0]["text"] == "response"
        assert result[2]["content"] == [{"type": "text", "text": "follow up"}]

    def test_strips_whitespace_only_text_block(self):
        """Whitespace-only text blocks should also be removed."""
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": " "},
                    {"type": "text", "text": "Hello!"},
                ],
            },
        ]
        result = _sanitize_messages(messages)
        assert len(result[0]["content"]) == 1
        assert result[0]["content"][0]["text"] == "Hello!"

    def test_strips_various_whitespace_patterns(self):
        """Tabs, newlines, and mixed whitespace should all be stripped."""
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "\t"},
                    {"type": "text", "text": "\n"},
                    {"type": "text", "text": "  \n\t  "},
                    {"type": "text", "text": "keep this"},
                ],
            },
        ]
        result = _sanitize_messages(messages)
        assert len(result[0]["content"]) == 1
        assert result[0]["content"][0]["text"] == "keep this"

    def test_handles_none_content(self):
        """Messages with None content should pass through unchanged."""
        messages = [{"role": "user", "content": None}]
        assert _sanitize_messages(messages) == messages

    def test_does_not_mutate_original(self):
        """Sanitization should not modify the original messages."""
        original_content = [
            {"type": "text", "text": ""},
            {"type": "text", "text": "keep"},
        ]
        messages = [{"role": "assistant", "content": original_content}]
        _sanitize_messages(messages)
        assert len(original_content) == 2


class TestCollectToolUseIds:
    """Test _collect_tool_use_ids function."""

    def test_collects_from_assistant_messages(self):
        """Collects tool_use IDs from assistant messages."""
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tu_1", "name": "bash", "input": {}},
                    {"type": "tool_use", "id": "tu_2", "name": "read", "input": {}},
                ],
            },
        ]
        assert _collect_tool_use_ids(messages) == {"tu_1", "tu_2"}

    def test_ignores_user_messages(self):
        """Does not collect IDs from non-assistant messages."""
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_1",
                        "content": "ok",
                    },
                ],
            },
        ]
        assert _collect_tool_use_ids(messages) == set()

    def test_handles_string_content(self):
        """Handles assistant messages with string content."""
        messages = [{"role": "assistant", "content": "Just text"}]
        assert _collect_tool_use_ids(messages) == set()

    def test_handles_empty_messages(self):
        """Handles empty message list."""
        assert _collect_tool_use_ids([]) == set()

    def test_collects_across_multiple_messages(self):
        """Collects IDs across multiple assistant messages."""
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu_1",
                        "name": "bash",
                        "input": {},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_1",
                        "content": "ok",
                    }
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu_2",
                        "name": "read",
                        "input": {},
                    }
                ],
            },
        ]
        assert _collect_tool_use_ids(messages) == {"tu_1", "tu_2"}


class TestPruneOrphanedToolResults:
    """Test _prune_orphaned_tool_results function."""

    def test_keeps_matched_tool_results(self):
        """Tool results with matching tool_use IDs are preserved."""
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu_1",
                        "name": "bash",
                        "input": {},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_1",
                        "content": "ok",
                    }
                ],
            },
        ]
        result = _prune_orphaned_tool_results(messages)
        assert len(result) == 2
        assert result[1]["content"][0]["tool_use_id"] == "tu_1"

    def test_removes_orphaned_tool_results(self):
        """Tool results without matching tool_use are removed."""
        messages = [
            {"role": "user", "content": "Hello"},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_orphan",
                        "content": "stale",
                    },
                    {"type": "text", "text": "Some other content"},
                ],
            },
        ]
        result = _prune_orphaned_tool_results(messages)
        assert len(result) == 2
        assert len(result[1]["content"]) == 1
        assert result[1]["content"][0]["type"] == "text"

    def test_drops_message_when_all_blocks_orphaned(self):
        """If all blocks in a message are orphaned, drop the entire message."""
        messages = [
            {"role": "user", "content": "Hello"},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_orphan1",
                        "content": "stale1",
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_orphan2",
                        "content": "stale2",
                    },
                ],
            },
        ]
        result = _prune_orphaned_tool_results(messages)
        assert len(result) == 1
        assert result[0]["content"] == "Hello"

    def test_preserves_non_tool_result_blocks(self):
        """Non-tool_result blocks are never pruned."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Hello"},
                    {
                        "type": "image",
                        "source": {
                            "type": "url",
                            "url": "https://example.com/img.png",
                        },
                    },
                ],
            },
        ]
        result = _prune_orphaned_tool_results(messages)
        assert len(result[0]["content"]) == 2

    def test_passthrough_string_content(self):
        """Messages with string content pass through unchanged."""
        messages = [{"role": "user", "content": "Hello"}]
        result = _prune_orphaned_tool_results(messages)
        assert result == messages

    def test_does_not_mutate_original(self):
        """Pruning should not modify the original messages."""
        original_content = [
            {
                "type": "tool_result",
                "tool_use_id": "tu_orphan",
                "content": "stale",
            },
            {"type": "text", "text": "keep"},
        ]
        messages = [{"role": "user", "content": original_content}]
        _prune_orphaned_tool_results(messages)
        assert len(original_content) == 2

    def test_complex_conversation_with_mixed_results(self):
        """Realistic conversation with both matched and orphaned tool results."""
        messages = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu_1",
                        "name": "bash",
                        "input": {"cmd": "ls"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_1",
                        "content": "file1.txt",
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_old",
                        "content": "stale data",
                    },
                ],
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "I see file1.txt"}],
            },
        ]
        result = _prune_orphaned_tool_results(messages)
        assert len(result) == 3
        assert len(result[1]["content"]) == 1
        assert result[1]["content"][0]["tool_use_id"] == "tu_1"


class TestDeduplicateTools:
    """Test _deduplicate_tools function."""

    def test_no_duplicates_unchanged(self):
        """Tools without duplicates pass through unchanged."""
        tools = [
            {"name": "bash", "description": "Run bash"},
            {"name": "read", "description": "Read file"},
        ]
        assert _deduplicate_tools(tools) == tools

    def test_removes_duplicate_tools(self):
        """Duplicate tool names are removed, keeping first occurrence."""
        tools = [
            {"name": "bash", "description": "Run bash v1"},
            {"name": "read", "description": "Read file"},
            {"name": "bash", "description": "Run bash v2"},
        ]
        result = _deduplicate_tools(tools)
        assert len(result) == 2
        assert result[0]["description"] == "Run bash v1"
        assert result[1]["description"] == "Read file"

    def test_empty_list(self):
        """Empty tools list passes through."""
        assert _deduplicate_tools([]) == []


class TestSanitizeRequest:
    """Test _sanitize_request function."""

    def test_applies_all_sanitizations(self):
        """Applies both message sanitization and tool deduplication."""
        kwargs = {
            "model": "claude-sonnet-4-20250514",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": ""},
                        {
                            "type": "tool_use",
                            "id": "tu_1",
                            "name": "bash",
                            "input": {},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_1",
                            "content": "ok",
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_orphan",
                            "content": "stale",
                        },
                    ],
                },
            ],
            "tools": [
                {"name": "bash", "description": "v1"},
                {"name": "bash", "description": "v2"},
            ],
            "max_tokens": 100,
        }
        result = _sanitize_request(kwargs)
        # Empty text block stripped
        assert len(result["messages"][0]["content"]) == 1
        assert result["messages"][0]["content"][0]["type"] == "tool_use"
        # Orphaned tool_result pruned
        assert len(result["messages"][1]["content"]) == 1
        assert result["messages"][1]["content"][0]["tool_use_id"] == "tu_1"
        # Tools deduplicated
        assert len(result["tools"]) == 1

    def test_no_messages_key(self):
        """Handles kwargs without messages key."""
        kwargs = {"model": "test", "max_tokens": 100}
        result = _sanitize_request(kwargs)
        assert result == kwargs

    def test_no_tools_key(self):
        """Handles kwargs without tools key."""
        kwargs = {
            "model": "test",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
        }
        result = _sanitize_request(kwargs)
        assert "tools" not in result


# ---------------------------------------------------------------------------
# Retry-with-fix tests
# ---------------------------------------------------------------------------


class TestTryAutoFix:
    """Test _try_auto_fix function."""

    def test_fixes_empty_text_blocks(self):
        """Fixes empty text block errors by sanitizing messages."""
        kwargs = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": ""},
                        {"type": "text", "text": "real content"},
                    ],
                },
            ],
        }
        error = _make_bad_request_error("messages: text content blocks must be non-empty")
        fixed = _try_auto_fix(kwargs, error)
        assert fixed is not None
        assert len(fixed["messages"][0]["content"]) == 1
        assert fixed["messages"][0]["content"][0]["text"] == "real content"

    def test_fixes_whitespace_text_blocks(self):
        """Fixes whitespace-only text block errors."""
        kwargs = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": " "},
                        {"type": "text", "text": "real"},
                    ],
                },
            ],
        }
        error = _make_bad_request_error("must contain non-whitespace text")
        fixed = _try_auto_fix(kwargs, error)
        assert fixed is not None
        assert len(fixed["messages"][0]["content"]) == 1

    def test_fixes_orphaned_tool_results(self):
        """Fixes tool_result mismatch errors by pruning orphaned results."""
        kwargs = {
            "messages": [
                {"role": "user", "content": "Hello"},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_orphan",
                            "content": "stale",
                        },
                        {"type": "text", "text": "keep"},
                    ],
                },
            ],
        }
        error = _make_bad_request_error("tool_use_id does not match any tool_use blocks")
        fixed = _try_auto_fix(kwargs, error)
        assert fixed is not None
        assert len(fixed["messages"][1]["content"]) == 1
        assert fixed["messages"][1]["content"][0]["type"] == "text"

    def test_returns_none_when_tool_result_pruning_changes_nothing(self):
        """Returns None if tool_result pruning doesn't help."""
        kwargs = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu_1",
                            "name": "bash",
                            "input": {},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_1",
                            "content": "ok",
                        }
                    ],
                },
            ],
        }
        error = _make_bad_request_error("tool_result error but all results match")
        fixed = _try_auto_fix(kwargs, error)
        assert fixed is None

    def test_fixes_duplicate_tools(self):
        """Fixes duplicate tool name errors by deduplicating."""
        kwargs = {
            "messages": [{"role": "user", "content": "Hello"}],
            "tools": [
                {"name": "bash", "description": "v1"},
                {"name": "bash", "description": "v2"},
            ],
        }
        error = _make_bad_request_error("Tool names must be unique")
        fixed = _try_auto_fix(kwargs, error)
        assert fixed is not None
        assert len(fixed["tools"]) == 1

    def test_returns_none_for_context_overflow(self):
        """Does not auto-fix context overflow errors."""
        kwargs = {"messages": [{"role": "user", "content": "Hello"}]}
        for msg in [
            "prompt is too long",
            "too many tokens in the request",
            "exceeds context length limit",
        ]:
            error = _make_bad_request_error(msg)
            assert _try_auto_fix(kwargs, error) is None

    def test_returns_none_for_unknown_400(self):
        """Does not auto-fix unknown 400 errors."""
        kwargs = {"messages": [{"role": "user", "content": "Hello"}]}
        error = _make_bad_request_error("some completely unknown error message")
        assert _try_auto_fix(kwargs, error) is None

    def test_returns_none_when_all_text_blocks_empty(self):
        """Returns None if all text blocks are empty (sanitization can't help)."""
        kwargs = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": ""},
                        {"type": "text", "text": "  "},
                    ],
                },
            ],
        }
        error = _make_bad_request_error("text content blocks must be non-empty")
        fixed = _try_auto_fix(kwargs, error)
        assert fixed is None

    def test_does_not_mutate_original_kwargs(self):
        """Auto-fix returns new dict, does not mutate original."""
        original_messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": ""},
                    {"type": "text", "text": "real"},
                ],
            },
        ]
        kwargs = {"messages": original_messages}
        error = _make_bad_request_error("text content blocks must be non-empty")
        fixed = _try_auto_fix(kwargs, error)
        assert fixed is not None
        assert len(kwargs["messages"][0]["content"]) == 2


# ---------------------------------------------------------------------------
# Human-centered error message tests
# ---------------------------------------------------------------------------


class TestIsContextOverflow:
    """Test _is_context_overflow detection."""

    @pytest.mark.parametrize(
        "message",
        [
            "prompt is too long",
            "Request has too many tokens",
            "exceeds context length",
            "The input exceeds the maximum number of tokens",
        ],
    )
    def test_detects_context_overflow(self, message: str):
        """Recognizes context overflow error messages."""
        error = _make_bad_request_error(message)
        assert _is_context_overflow(error) is True

    def test_not_context_overflow(self):
        """Does not match non-overflow errors."""
        error = _make_bad_request_error("text content blocks must be non-empty")
        assert _is_context_overflow(error) is False


# ---------------------------------------------------------------------------
# Integration: retry-with-fix in complete() and stream()
# ---------------------------------------------------------------------------


class TestCompleteRetryWithFix:
    """Test retry-with-fix behavior in complete()."""

    @pytest.mark.asyncio
    async def test_retries_on_fixable_error(self, sample_message: Message):
        """complete() retries once when a fixable 400 occurs.

        Uses patch to bypass pre-flight sanitization so the empty text block
        survives to trigger the retry-with-fix path.
        """
        from unittest.mock import patch

        client = AnthropicClient(api_key="test-key")
        request = AnthropicRequest(
            model="claude-sonnet-4-20250514",
            messages=[
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": ""},
                        {"type": "text", "text": "real content"},
                    ],
                },
                {"role": "user", "content": "Hello"},
            ],
            max_tokens=100,
        )

        mock_async_client = AsyncMock()
        mock_async_client.messages.create = AsyncMock(
            side_effect=[
                _make_bad_request_error("text content blocks must be non-empty"),
                sample_message,
            ]
        )
        client._client = mock_async_client

        # Bypass pre-flight so the empty block survives to trigger retry
        with patch(
            "luthien_proxy.llm.anthropic_client._sanitize_request",
            side_effect=lambda kwargs, on_fix=None: kwargs,
        ):
            result = await client.complete(request)
        assert result["id"] == "msg_123"
        assert mock_async_client.messages.create.call_count == 2

    @pytest.mark.asyncio
    async def test_max_one_retry(self):
        """complete() retries at most once — second failure gets human-centered message."""
        from unittest.mock import patch

        client = AnthropicClient(api_key="test-key")
        request = AnthropicRequest(
            model="claude-sonnet-4-20250514",
            messages=[
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": ""},
                        {"type": "text", "text": "real"},
                    ],
                },
                {"role": "user", "content": "Hello"},
            ],
            max_tokens=100,
        )

        error = _make_bad_request_error("text content blocks must be non-empty")
        mock_async_client = AsyncMock()
        mock_async_client.messages.create = AsyncMock(side_effect=[error, error])
        client._client = mock_async_client

        # Bypass pre-flight so the empty block survives to trigger retry
        with patch(
            "luthien_proxy.llm.anthropic_client._sanitize_request",
            side_effect=lambda kwargs, on_fix=None: kwargs,
        ):
            with pytest.raises(anthropic.BadRequestError) as exc_info:
                await client.complete(request)

        # Second failure should get human-centered error message
        assert "Luthien couldn't process" in str(exc_info.value.message)
        assert mock_async_client.messages.create.call_count == 2

    @pytest.mark.asyncio
    async def test_unfixable_error_raises_with_human_message(self):
        """Unfixable 400s raise with human-centered error message."""
        client = AnthropicClient(api_key="test-key")
        request = AnthropicRequest(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
        )

        mock_async_client = AsyncMock()
        mock_async_client.messages.create = AsyncMock(
            side_effect=_make_bad_request_error("prompt is too long for this model")
        )
        client._client = mock_async_client

        with pytest.raises(anthropic.BadRequestError) as exc_info:
            await client.complete(request)

        assert "/compact" in str(exc_info.value.message)
        assert "claude-sonnet-4-20250514" in str(exc_info.value.message)
        assert mock_async_client.messages.create.call_count == 1

    @pytest.mark.asyncio
    async def test_unknown_400_gets_generic_human_message(self):
        """Unknown 400 errors get a generic helpful message."""
        client = AnthropicClient(api_key="test-key")
        request = AnthropicRequest(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
        )

        mock_async_client = AsyncMock()
        mock_async_client.messages.create = AsyncMock(side_effect=_make_bad_request_error("some weird unknown error"))
        client._client = mock_async_client

        with pytest.raises(anthropic.BadRequestError) as exc_info:
            await client.complete(request)

        assert "Luthien couldn't process" in str(exc_info.value.message)
        assert "luthien-proxy/issues" in str(exc_info.value.message)

    @pytest.mark.asyncio
    async def test_server_error_gets_human_message(self):
        """5xx errors get a human-centered retry message."""
        client = AnthropicClient(api_key="test-key")
        request = AnthropicRequest(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
        )

        mock_async_client = AsyncMock()
        mock_async_client.messages.create = AsyncMock(side_effect=_make_internal_server_error())
        client._client = mock_async_client

        with pytest.raises(anthropic.InternalServerError) as exc_info:
            await client.complete(request)

        assert "temporarily unavailable" in str(exc_info.value.message)
        assert "claude-sonnet-4-20250514" in str(exc_info.value.message)


class TestStreamRetryWithFix:
    """Test retry-with-fix behavior in stream()."""

    @pytest.mark.asyncio
    async def test_retries_on_fixable_error(self, sample_stream_events: list):
        """stream() retries once when a fixable 400 occurs."""
        from unittest.mock import patch

        client = AnthropicClient(api_key="test-key")
        request = AnthropicRequest(
            model="claude-sonnet-4-20250514",
            messages=[
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": ""},
                        {"type": "text", "text": "real"},
                    ],
                },
                {"role": "user", "content": "Hello"},
            ],
            max_tokens=100,
        )

        async def mock_stream_iter():
            for event in sample_stream_events:
                yield event

        error = _make_bad_request_error("text content blocks must be non-empty")

        call_count = 0

        def mock_stream_factory(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                mock_stream = MagicMock()
                mock_stream.__aenter__ = AsyncMock(side_effect=error)
                mock_stream.__aexit__ = AsyncMock(return_value=None)
                return mock_stream
            else:
                mock_stream = MagicMock()
                mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
                mock_stream.__aexit__ = AsyncMock(return_value=None)
                mock_stream.__aiter__ = lambda self: mock_stream_iter()
                return mock_stream

        mock_async_client = AsyncMock()
        mock_async_client.messages.stream = MagicMock(side_effect=mock_stream_factory)
        client._client = mock_async_client

        # Bypass pre-flight so the empty block survives to trigger retry
        with patch(
            "luthien_proxy.llm.anthropic_client._sanitize_request",
            side_effect=lambda kwargs, on_fix=None: kwargs,
        ):
            events = []
            async for event in client.stream(request):
                events.append(event)

        assert len(events) == 7
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_unfixable_error_raises_with_human_message(self):
        """Unfixable 400s in stream() raise with human-centered message."""
        client = AnthropicClient(api_key="test-key")
        request = AnthropicRequest(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
        )

        error = _make_bad_request_error("prompt is too long")

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(side_effect=error)
        mock_stream.__aexit__ = AsyncMock(return_value=None)

        mock_async_client = AsyncMock()
        mock_async_client.messages.stream = MagicMock(return_value=mock_stream)
        client._client = mock_async_client

        with pytest.raises(anthropic.BadRequestError) as exc_info:
            async for _ in client.stream(request):
                pass

        assert "/compact" in str(exc_info.value.message)

    @pytest.mark.asyncio
    async def test_server_error_gets_human_message(self):
        """5xx errors in stream() get a human-centered message."""
        client = AnthropicClient(api_key="test-key")
        request = AnthropicRequest(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
        )

        error = _make_internal_server_error()

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(side_effect=error)
        mock_stream.__aexit__ = AsyncMock(return_value=None)

        mock_async_client = AsyncMock()
        mock_async_client.messages.stream = MagicMock(return_value=mock_stream)
        client._client = mock_async_client

        with pytest.raises(anthropic.InternalServerError) as exc_info:
            async for _ in client.stream(request):
                pass

        assert "temporarily unavailable" in str(exc_info.value.message)


# ---------------------------------------------------------------------------
# AutoFixCallback tests
# ---------------------------------------------------------------------------


class TestAutoFixCallback:
    """Test that on_auto_fix callback is invoked when fixes are applied."""

    def test_sanitize_request_calls_on_fix_for_empty_text(self):
        """on_fix callback fires when empty text blocks are stripped."""
        fixes: list[tuple[str, dict]] = []

        kwargs = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": ""},
                        {"type": "text", "text": "keep"},
                    ],
                },
            ],
        }
        _sanitize_request(kwargs, on_fix=lambda t, d: fixes.append((t, d)))
        assert len(fixes) == 1
        assert fixes[0][0] == "empty_text_blocks_stripped"
        assert fixes[0][1]["phase"] == "pre-flight"

    def test_sanitize_request_calls_on_fix_for_orphaned_tool_results(self):
        """on_fix callback fires when orphaned tool_results are pruned."""
        fixes: list[tuple[str, dict]] = []

        kwargs = {
            "messages": [
                {"role": "user", "content": "Hello"},
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "orphan", "content": "x"},
                        {"type": "text", "text": "keep"},
                    ],
                },
            ],
        }
        _sanitize_request(kwargs, on_fix=lambda t, d: fixes.append((t, d)))
        assert len(fixes) == 1
        assert fixes[0][0] == "orphaned_tool_results_pruned"

    def test_sanitize_request_calls_on_fix_for_duplicate_tools(self):
        """on_fix callback fires when duplicate tools are removed."""
        fixes: list[tuple[str, dict]] = []

        kwargs = {
            "messages": [{"role": "user", "content": "Hello"}],
            "tools": [
                {"name": "bash", "description": "v1"},
                {"name": "bash", "description": "v2"},
            ],
        }
        _sanitize_request(kwargs, on_fix=lambda t, d: fixes.append((t, d)))
        assert len(fixes) == 1
        assert fixes[0][0] == "duplicate_tools_removed"

    def test_sanitize_request_no_callback_when_no_fixes(self):
        """on_fix callback is NOT called when no fixes are needed."""
        fixes: list[tuple[str, dict]] = []

        kwargs = {
            "messages": [{"role": "user", "content": "Hello"}],
        }
        _sanitize_request(kwargs, on_fix=lambda t, d: fixes.append((t, d)))
        assert len(fixes) == 0

    def test_sanitize_request_multiple_fixes_fire_multiple_callbacks(self):
        """All applicable fixes fire their own callback."""
        fixes: list[tuple[str, dict]] = []

        kwargs = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": ""},
                        {"type": "tool_use", "id": "tu_1", "name": "bash", "input": {}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"},
                        {"type": "tool_result", "tool_use_id": "orphan", "content": "stale"},
                    ],
                },
            ],
            "tools": [
                {"name": "bash", "description": "v1"},
                {"name": "bash", "description": "v2"},
            ],
        }
        _sanitize_request(kwargs, on_fix=lambda t, d: fixes.append((t, d)))
        fix_types = [f[0] for f in fixes]
        assert "empty_text_blocks_stripped" in fix_types
        assert "orphaned_tool_results_pruned" in fix_types
        assert "duplicate_tools_removed" in fix_types

    @pytest.mark.asyncio
    async def test_complete_calls_on_auto_fix_on_preflight(self):
        """complete() passes on_auto_fix through to pre-flight sanitization."""
        fixes: list[tuple[str, dict]] = []
        client = AnthropicClient(api_key="test-key")

        request = AnthropicRequest(
            model="claude-sonnet-4-20250514",
            messages=[
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": ""},
                        {"type": "text", "text": "keep"},
                    ],
                },
                {"role": "user", "content": "follow up"},
            ],
            max_tokens=100,
        )

        sample_message = Message(
            id="msg_123",
            type="message",
            role="assistant",
            content=[TextBlock(type="text", text="Hi!")],
            model="claude-sonnet-4-20250514",
            stop_reason="end_turn",
            usage=Usage(input_tokens=10, output_tokens=5),
        )

        mock_async_client = AsyncMock()
        mock_async_client.messages.create = AsyncMock(return_value=sample_message)
        client._client = mock_async_client

        await client.complete(request, on_auto_fix=lambda t, d: fixes.append((t, d)))
        assert len(fixes) == 1
        assert fixes[0][0] == "empty_text_blocks_stripped"

    @pytest.mark.asyncio
    async def test_complete_calls_on_auto_fix_on_retry(self):
        """complete() fires on_auto_fix callback when retry-with-fix succeeds."""
        fixes: list[tuple[str, dict]] = []
        client = AnthropicClient(api_key="test-key")

        sample_message = Message(
            id="msg_123",
            type="message",
            role="assistant",
            content=[TextBlock(type="text", text="Hi!")],
            model="claude-sonnet-4-20250514",
            stop_reason="end_turn",
            usage=Usage(input_tokens=10, output_tokens=5),
        )

        error = _make_bad_request_error("Tool names must be unique")

        mock_async_client = AsyncMock()
        mock_async_client.messages.create = AsyncMock(side_effect=[error, sample_message])
        client._client = mock_async_client

        request_with_tools = AnthropicRequest(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
            tools=[
                {"name": "bash", "description": "v1", "input_schema": {}},
                {"name": "bash", "description": "v2", "input_schema": {}},
            ],
        )

        await client.complete(request_with_tools, on_auto_fix=lambda t, d: fixes.append((t, d)))

        fix_types = [f[0] for f in fixes]
        assert "duplicate_tools_removed" in fix_types
        assert "retry_with_fix" in fix_types


# ---------------------------------------------------------------------------
# Passthrough methods
# ---------------------------------------------------------------------------


class TestCompletePassthrough:
    """Test complete_passthrough method."""

    @pytest.mark.asyncio
    async def test_sends_request_directly(self, sample_message: Message):
        """Passthrough sends raw request body with no sanitization."""
        client = AnthropicClient(api_key="test-key")
        mock_async_client = AsyncMock()
        mock_async_client.messages.create = AsyncMock(return_value=sample_message)
        client._client = mock_async_client

        body = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
        }

        result = await client.complete_passthrough(body)
        assert result["id"] == "msg_123"
        mock_async_client.messages.create.assert_called_once_with(
            model="claude-sonnet-4-20250514",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=100,
        )

    @pytest.mark.asyncio
    async def test_filters_unknown_kwargs(self, sample_message: Message):
        """Passthrough only passes known API kwargs, filtering out extras."""
        client = AnthropicClient(api_key="test-key")
        mock_async_client = AsyncMock()
        mock_async_client.messages.create = AsyncMock(return_value=sample_message)
        client._client = mock_async_client

        body = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
            "context_management": {"enabled": True},  # Non-API field
            "custom_field": "should be stripped",
        }

        await client.complete_passthrough(body)
        call_kwargs = mock_async_client.messages.create.call_args[1]
        assert "context_management" not in call_kwargs
        assert "custom_field" not in call_kwargs

    @pytest.mark.asyncio
    async def test_strips_stream_param(self, sample_message: Message):
        """Passthrough strips 'stream' from kwargs for complete()."""
        client = AnthropicClient(api_key="test-key")
        mock_async_client = AsyncMock()
        mock_async_client.messages.create = AsyncMock(return_value=sample_message)
        client._client = mock_async_client

        body = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
            "stream": False,
        }

        await client.complete_passthrough(body)
        call_kwargs = mock_async_client.messages.create.call_args[1]
        assert "stream" not in call_kwargs

    @pytest.mark.asyncio
    async def test_no_sanitization_applied(self):
        """Passthrough does NOT sanitize — empty text blocks pass through."""
        client = AnthropicClient(api_key="test-key")
        mock_async_client = AsyncMock()
        mock_async_client.messages.create = AsyncMock(
            side_effect=anthropic.BadRequestError(
                message="text content blocks must be non-empty",
                response=httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com")),
                body=None,
            )
        )
        client._client = mock_async_client

        body = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": [{"type": "text", "text": ""}]}],
            "max_tokens": 100,
        }

        with pytest.raises(anthropic.BadRequestError):
            await client.complete_passthrough(body)

        # Verify the empty text block was NOT stripped (no sanitization)
        call_kwargs = mock_async_client.messages.create.call_args[1]
        assert call_kwargs["messages"] == body["messages"]


class TestStreamPassthrough:
    """Test stream_passthrough method."""

    @pytest.mark.asyncio
    async def test_streams_events_directly(self, sample_stream_events: list):
        """Passthrough streams events from the SDK."""
        client = AnthropicClient(api_key="test-key")

        async def mock_stream_iter():
            for event in sample_stream_events:
                yield event

        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_stream_ctx)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_stream_ctx.__aiter__ = lambda self: mock_stream_iter()

        mock_async_client = AsyncMock()
        mock_async_client.messages.stream = MagicMock(return_value=mock_stream_ctx)
        client._client = mock_async_client

        body = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
        }

        events = []
        async for event in client.stream_passthrough(body):
            events.append(event)

        assert len(events) == len(sample_stream_events)

    @pytest.mark.asyncio
    async def test_filters_unknown_kwargs(self, sample_stream_events: list):
        """Passthrough filters non-API kwargs for streaming too."""
        client = AnthropicClient(api_key="test-key")

        async def mock_stream_iter():
            for event in sample_stream_events:
                yield event

        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_stream_ctx)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_stream_ctx.__aiter__ = lambda self: mock_stream_iter()

        mock_async_client = AsyncMock()
        mock_async_client.messages.stream = MagicMock(return_value=mock_stream_ctx)
        client._client = mock_async_client

        body = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
            "context_management": {"enabled": True},
            "stream": True,
        }

        events = []
        async for event in client.stream_passthrough(body):
            events.append(event)

        call_kwargs = mock_async_client.messages.stream.call_args[1]
        assert "context_management" not in call_kwargs
        assert "stream" not in call_kwargs
