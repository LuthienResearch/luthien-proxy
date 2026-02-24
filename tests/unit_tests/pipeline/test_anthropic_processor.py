"""Unit tests for the Anthropic-native pipeline processor module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anthropic import APIConnectionError as AnthropicConnectionError
from anthropic import APIStatusError as AnthropicStatusError
from anthropic.types import (
    RawContentBlockDeltaEvent,
    RawMessageStartEvent,
    RawMessageStopEvent,
    TextDelta,
)
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse as FastAPIStreamingResponse
from httpx import Request as HttpxRequest
from httpx import Response as HttpxResponse
from tests.constants import DEFAULT_CLAUDE_TEST_MODEL

from luthien_proxy.llm.types.anthropic import AnthropicRequest, AnthropicResponse
from luthien_proxy.pipeline.anthropic_processor import (
    _build_error_event,
    _format_sse_event,
    _handle_non_streaming,
    _handle_streaming,
    _process_request,
    process_anthropic_request,
)
from luthien_proxy.policies.noop_policy import NoOpPolicy


class TestFormatSSEEvent:
    """Tests for _format_sse_event helper function."""

    def test_formats_message_start_event(self):
        """Test formatting a message_start event."""
        event = RawMessageStartEvent(
            type="message_start",
            message={
                "id": "msg_123",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": DEFAULT_CLAUDE_TEST_MODEL,
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 0},
            },
        )
        result = _format_sse_event(event)

        assert result.startswith("event: message_start\n")
        assert "data: " in result
        assert result.endswith("\n\n")
        assert '"type": "message_start"' in result

    def test_formats_content_block_delta_event(self):
        """Test formatting a content_block_delta event."""
        event = RawContentBlockDeltaEvent(
            type="content_block_delta",
            index=0,
            delta=TextDelta(type="text_delta", text="Hello"),
        )
        result = _format_sse_event(event)

        assert result.startswith("event: content_block_delta\n")
        assert '"text": "Hello"' in result
        assert result.endswith("\n\n")

    def test_formats_message_stop_event(self):
        """Test formatting a message_stop event."""
        event = RawMessageStopEvent(type="message_stop")
        result = _format_sse_event(event)

        assert result == 'event: message_stop\ndata: {"type": "message_stop"}\n\n'

    def test_handles_unknown_event_type(self):
        """Test handling event with missing type."""
        event = {"some_field": "value"}  # No type field
        result = _format_sse_event(event)  # type: ignore[arg-type]

        assert result.startswith("event: unknown\n")


class TestProcessRequest:
    """Tests for _process_request helper function."""

    @pytest.fixture
    def mock_request(self):
        """Create a mock FastAPI request."""
        request = MagicMock()
        request.headers = {}
        request.method = "POST"
        request.url = MagicMock()
        request.url.path = "/v1/messages"
        return request

    @pytest.fixture
    def mock_emitter(self):
        """Create a mock event emitter."""
        return MagicMock()

    @pytest.fixture
    def mock_span(self):
        """Create a mock OpenTelemetry span."""
        span = MagicMock()
        span.set_attribute = MagicMock()
        span.add_event = MagicMock()
        return span

    @pytest.mark.asyncio
    async def test_valid_anthropic_request_parsing(self, mock_request, mock_emitter, mock_span):
        """Test parsing a valid Anthropic format request."""
        anthropic_body = {
            "model": DEFAULT_CLAUDE_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
            "stream": False,
        }
        mock_request.json = AsyncMock(return_value=anthropic_body)

        with patch("luthien_proxy.pipeline.anthropic_processor.tracer") as mock_tracer:
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            anthropic_request, raw_http_request, session_id = await _process_request(
                request=mock_request,
                call_id="test-call-id",
                emitter=mock_emitter,
            )

        assert anthropic_request["model"] == DEFAULT_CLAUDE_TEST_MODEL
        assert anthropic_request["max_tokens"] == 1024
        assert anthropic_request.get("stream") is False
        assert raw_http_request.body == anthropic_body
        assert raw_http_request.method == "POST"
        assert raw_http_request.path == "/v1/messages"
        assert session_id is None
        mock_emitter.record.assert_called()

    @pytest.mark.asyncio
    async def test_extracts_session_id_from_metadata(self, mock_request, mock_emitter, mock_span):
        """Test extracting session ID from metadata.user_id field."""
        # Session ID pattern expects hex UUID format: _session_<hex-uuid>
        anthropic_body = {
            "model": DEFAULT_CLAUDE_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
            "metadata": {"user_id": "user_abc123_account__session_a1b2c3d4-e5f6-7890-abcd-ef1234567890"},
        }
        mock_request.json = AsyncMock(return_value=anthropic_body)

        with patch("luthien_proxy.pipeline.anthropic_processor.tracer") as mock_tracer:
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            _anthropic_request, _raw_http_request, session_id = await _process_request(
                request=mock_request,
                call_id="test-call-id",
                emitter=mock_emitter,
            )

        assert session_id == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    @pytest.mark.asyncio
    async def test_request_size_limit_exceeded(self, mock_request, mock_emitter, mock_span):
        """Test that oversized requests raise HTTPException."""
        mock_request.headers = {"content-length": "999999999"}
        mock_request.json = AsyncMock(return_value={})

        with patch("luthien_proxy.pipeline.anthropic_processor.tracer") as mock_tracer:
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(HTTPException) as exc_info:
                await _process_request(
                    request=mock_request,
                    call_id="test-call-id",
                    emitter=mock_emitter,
                )

        assert exc_info.value.status_code == 413
        assert "payload too large" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_missing_model_returns_400(self, mock_request, mock_emitter, mock_span):
        """Test that missing model field returns 400 error."""
        invalid_body = {
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
        }
        mock_request.json = AsyncMock(return_value=invalid_body)

        with patch("luthien_proxy.pipeline.anthropic_processor.tracer") as mock_tracer:
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(HTTPException) as exc_info:
                await _process_request(
                    request=mock_request,
                    call_id="test-call-id",
                    emitter=mock_emitter,
                )

        assert exc_info.value.status_code == 400
        assert "model" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_missing_messages_returns_400(self, mock_request, mock_emitter, mock_span):
        """Test that missing messages field returns 400 error."""
        invalid_body = {
            "model": DEFAULT_CLAUDE_TEST_MODEL,
            "max_tokens": 1024,
        }
        mock_request.json = AsyncMock(return_value=invalid_body)

        with patch("luthien_proxy.pipeline.anthropic_processor.tracer") as mock_tracer:
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(HTTPException) as exc_info:
                await _process_request(
                    request=mock_request,
                    call_id="test-call-id",
                    emitter=mock_emitter,
                )

        assert exc_info.value.status_code == 400
        assert "messages" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_missing_max_tokens_returns_400(self, mock_request, mock_emitter, mock_span):
        """Test that missing max_tokens field returns 400 error."""
        invalid_body = {
            "model": DEFAULT_CLAUDE_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
        }
        mock_request.json = AsyncMock(return_value=invalid_body)

        with patch("luthien_proxy.pipeline.anthropic_processor.tracer") as mock_tracer:
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(HTTPException) as exc_info:
                await _process_request(
                    request=mock_request,
                    call_id="test-call-id",
                    emitter=mock_emitter,
                )

        assert exc_info.value.status_code == 400
        assert "max_tokens" in exc_info.value.detail.lower()


class TestHandleNonStreaming:
    """Tests for _handle_non_streaming helper function."""

    @pytest.fixture
    def mock_anthropic_response(self) -> AnthropicResponse:
        """Create a mock Anthropic response."""
        return AnthropicResponse(
            id="msg_test123",
            type="message",
            role="assistant",
            content=[{"type": "text", "text": "Hello there!"}],
            model=DEFAULT_CLAUDE_TEST_MODEL,
            stop_reason="end_turn",
            stop_sequence=None,
            usage={"input_tokens": 10, "output_tokens": 5},
        )

    @pytest.fixture
    def mock_anthropic_client(self, mock_anthropic_response):
        """Create a mock AnthropicClient."""
        client = MagicMock()
        client.complete = AsyncMock(return_value=mock_anthropic_response)
        return client

    @pytest.fixture
    def mock_policy(self, mock_anthropic_response):
        """Create an Anthropic policy for testing."""
        # Use the real NoOpPolicy which implements the interface
        return NoOpPolicy()

    @pytest.fixture
    def mock_policy_ctx(self):
        """Create a mock PolicyContext."""
        ctx = MagicMock()
        ctx.session_id = None
        return ctx

    @pytest.fixture
    def mock_emitter(self):
        """Create a mock event emitter."""
        return MagicMock()

    @pytest.mark.asyncio
    async def test_non_streaming_returns_json_response(
        self, mock_anthropic_client, mock_policy, mock_policy_ctx, mock_emitter, mock_anthropic_response
    ):
        """Test non-streaming response returns JSONResponse."""
        request: AnthropicRequest = {
            "model": DEFAULT_CLAUDE_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 1024,
        }

        with patch("luthien_proxy.pipeline.anthropic_processor.tracer") as mock_tracer:
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            response = await _handle_non_streaming(
                final_request=request,
                policy=mock_policy,
                policy_ctx=mock_policy_ctx,
                anthropic_client=mock_anthropic_client,
                emitter=mock_emitter,
                call_id="test-call-id",
            )

        assert isinstance(response, JSONResponse)
        assert response.headers.get("x-call-id") == "test-call-id"
        mock_anthropic_client.complete.assert_called_once_with(request)

    @pytest.mark.asyncio
    async def test_emits_client_response_event(
        self, mock_anthropic_client, mock_policy, mock_policy_ctx, mock_emitter, mock_anthropic_response
    ):
        """Test that client response event is emitted."""
        request: AnthropicRequest = {
            "model": DEFAULT_CLAUDE_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 1024,
        }

        with patch("luthien_proxy.pipeline.anthropic_processor.tracer") as mock_tracer:
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            await _handle_non_streaming(
                final_request=request,
                policy=mock_policy,
                policy_ctx=mock_policy_ctx,
                anthropic_client=mock_anthropic_client,
                emitter=mock_emitter,
                call_id="test-call-id",
            )

        mock_emitter.record.assert_called()
        call_args = mock_emitter.record.call_args
        assert call_args[0][0] == "test-call-id"
        assert call_args[0][1] == "pipeline.client_response"


class TestHandleStreaming:
    """Tests for _handle_streaming helper function."""

    @pytest.fixture
    def mock_anthropic_client(self):
        """Create a mock AnthropicClient with stream method."""
        client = MagicMock()

        async def mock_stream(request):
            # Yield a few events using SDK types
            yield RawMessageStartEvent(
                type="message_start",
                message={
                    "id": "msg_123",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": "claude-3-5-sonnet-20241022",
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            )
            yield RawMessageStopEvent(type="message_stop")

        client.stream = mock_stream
        return client

    @pytest.fixture
    def mock_policy(self):
        """Create a mock Anthropic policy."""
        policy = NoOpPolicy()
        return policy

    @pytest.fixture
    def mock_policy_ctx(self):
        """Create a mock PolicyContext."""
        ctx = MagicMock()
        ctx.response_summary = None
        return ctx

    @pytest.mark.asyncio
    async def test_streaming_returns_streaming_response(self, mock_anthropic_client, mock_policy, mock_policy_ctx):
        """Test streaming handler returns FastAPIStreamingResponse."""
        request: AnthropicRequest = {
            "model": DEFAULT_CLAUDE_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 1024,
            "stream": True,
        }

        with patch("luthien_proxy.pipeline.anthropic_processor.tracer") as mock_tracer:
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            mock_root_span = MagicMock()

            response = await _handle_streaming(
                final_request=request,
                policy=mock_policy,
                policy_ctx=mock_policy_ctx,
                anthropic_client=mock_anthropic_client,
                call_id="test-call-id",
                root_span=mock_root_span,
            )

        assert isinstance(response, FastAPIStreamingResponse)
        assert response.media_type == "text/event-stream"
        assert response.headers.get("cache-control") == "no-cache"
        assert response.headers.get("x-call-id") == "test-call-id"


class TestProcessAnthropicRequest:
    """Integration tests for the main process_anthropic_request function."""

    @pytest.fixture
    def mock_request(self):
        """Create a mock FastAPI request."""
        request = MagicMock()
        request.headers = {}
        request.method = "POST"
        request.url = MagicMock()
        request.url.path = "/v1/messages"
        return request

    @pytest.fixture
    def mock_policy(self):
        """Create an Anthropic policy implementing AnthropicPolicyInterface."""
        return NoOpPolicy()

    @pytest.fixture
    def mock_anthropic_client(self):
        """Create a mock AnthropicClient."""
        response = AnthropicResponse(
            id="msg_test123",
            type="message",
            role="assistant",
            content=[{"type": "text", "text": "Hello!"}],
            model=DEFAULT_CLAUDE_TEST_MODEL,
            stop_reason="end_turn",
            stop_sequence=None,
            usage={"input_tokens": 10, "output_tokens": 5},
        )
        client = MagicMock()
        client.complete = AsyncMock(return_value=response)
        return client

    @pytest.fixture
    def mock_emitter(self):
        """Create a mock event emitter."""
        return MagicMock()

    @pytest.mark.asyncio
    async def test_non_streaming_request_end_to_end(
        self, mock_request, mock_policy, mock_anthropic_client, mock_emitter
    ):
        """Test processing a non-streaming Anthropic request end-to-end."""
        anthropic_body = {
            "model": DEFAULT_CLAUDE_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
            "stream": False,
        }
        mock_request.json = AsyncMock(return_value=anthropic_body)

        with patch("luthien_proxy.pipeline.anthropic_processor.tracer") as mock_tracer:
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            response = await process_anthropic_request(
                request=mock_request,
                policy=mock_policy,
                anthropic_client=mock_anthropic_client,
                emitter=mock_emitter,
            )

        assert isinstance(response, JSONResponse)
        mock_anthropic_client.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_streaming_request_returns_streaming_response(self, mock_request, mock_policy, mock_emitter):
        """Test streaming request returns StreamingResponse."""
        anthropic_body = {
            "model": DEFAULT_CLAUDE_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
            "stream": True,
        }
        mock_request.json = AsyncMock(return_value=anthropic_body)

        # Create streaming client
        async def mock_stream(request):
            yield RawMessageStartEvent(
                type="message_start",
                message={
                    "id": "msg_123",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": "claude-3-5-sonnet-20241022",
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            )
            yield RawMessageStopEvent(type="message_stop")

        mock_streaming_client = MagicMock()
        mock_streaming_client.stream = mock_stream

        with patch("luthien_proxy.pipeline.anthropic_processor.tracer") as mock_tracer:
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            response = await process_anthropic_request(
                request=mock_request,
                policy=mock_policy,
                anthropic_client=mock_streaming_client,
                emitter=mock_emitter,
            )

        assert isinstance(response, FastAPIStreamingResponse)

    @pytest.mark.asyncio
    async def test_request_too_large_raises_413(self, mock_request, mock_policy, mock_anthropic_client, mock_emitter):
        """Test oversized request returns 413 error."""
        mock_request.headers = {"content-length": "999999999"}
        mock_request.json = AsyncMock(return_value={})

        with patch("luthien_proxy.pipeline.anthropic_processor.tracer") as mock_tracer:
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(HTTPException) as exc_info:
                await process_anthropic_request(
                    request=mock_request,
                    policy=mock_policy,
                    anthropic_client=mock_anthropic_client,
                    emitter=mock_emitter,
                )

        assert exc_info.value.status_code == 413

    @pytest.mark.asyncio
    async def test_span_attributes_set_correctly(self, mock_request, mock_policy, mock_anthropic_client, mock_emitter):
        """Test that span attributes are set correctly."""
        anthropic_body = {
            "model": DEFAULT_CLAUDE_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
            "stream": False,
        }
        mock_request.json = AsyncMock(return_value=anthropic_body)

        with patch("luthien_proxy.pipeline.anthropic_processor.tracer") as mock_tracer:
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            await process_anthropic_request(
                request=mock_request,
                policy=mock_policy,
                anthropic_client=mock_anthropic_client,
                emitter=mock_emitter,
            )

        # Check that span attributes were set
        set_attribute_calls = [call[0] for call in mock_span.set_attribute.call_args_list]
        attribute_names = [call[0] for call in set_attribute_calls]

        assert "luthien.transaction_id" in attribute_names
        assert "luthien.client_format" in attribute_names
        assert "luthien.endpoint" in attribute_names
        assert "luthien.model" in attribute_names
        assert "luthien.stream" in attribute_names

    @pytest.mark.asyncio
    async def test_client_format_is_anthropic_native(
        self, mock_request, mock_policy, mock_anthropic_client, mock_emitter
    ):
        """Test that client_format span attribute is 'anthropic_native'."""
        anthropic_body = {
            "model": DEFAULT_CLAUDE_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
            "stream": False,
        }
        mock_request.json = AsyncMock(return_value=anthropic_body)

        with patch("luthien_proxy.pipeline.anthropic_processor.tracer") as mock_tracer:
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            await process_anthropic_request(
                request=mock_request,
                policy=mock_policy,
                anthropic_client=mock_anthropic_client,
                emitter=mock_emitter,
            )

        mock_span.set_attribute.assert_any_call("luthien.client_format", "anthropic_native")
        mock_span.set_attribute.assert_any_call("luthien.endpoint", "/v1/messages")


class TestBuildErrorEvent:
    """Tests for _build_error_event helper function."""

    def test_builds_api_status_error_event(self):
        """Test building error event from AnthropicStatusError."""
        mock_request = HttpxRequest("POST", "https://api.anthropic.com/v1/messages")
        mock_response = HttpxResponse(429, request=mock_request)
        error = AnthropicStatusError(
            message="Rate limit exceeded",
            response=mock_response,
            body=None,
        )

        event = _build_error_event(error, "test-call-id")

        assert event.get("type") == "error"
        assert event.get("error", {}).get("type") == "api_error"
        assert "Rate limit exceeded" in event.get("error", {}).get("message", "")

    def test_builds_connection_error_event(self):
        """Test building error event from AnthropicConnectionError."""
        mock_request = HttpxRequest("POST", "https://api.anthropic.com/v1/messages")
        error = AnthropicConnectionError(request=mock_request)

        event = _build_error_event(error, "test-call-id")

        assert event.get("type") == "error"
        assert event.get("error", {}).get("type") == "api_connection_error"

    def test_builds_generic_error_event(self):
        """Test building error event from unknown exception type."""
        error = RuntimeError("Something went wrong")

        event = _build_error_event(error, "test-call-id")

        assert event.get("type") == "error"
        assert event.get("error", {}).get("type") == "api_error"
        assert "Something went wrong" in event.get("error", {}).get("message", "")


class TestMidStreamErrorHandling:
    """Tests for mid-stream error handling in streaming responses."""

    @pytest.fixture
    def mock_policy(self):
        """Create a mock Anthropic policy."""
        return NoOpPolicy()

    @pytest.fixture
    def mock_policy_ctx(self):
        """Create a mock PolicyContext."""
        ctx = MagicMock()
        ctx.response_summary = None
        return ctx

    @pytest.mark.asyncio
    async def test_mid_stream_api_error_emits_error_event(self, mock_policy, mock_policy_ctx):
        """Test that API errors mid-stream emit an error event instead of raising."""
        request: AnthropicRequest = {
            "model": DEFAULT_CLAUDE_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 1024,
            "stream": True,
        }

        mock_request = HttpxRequest("POST", "https://api.anthropic.com/v1/messages")
        mock_response = HttpxResponse(500, request=mock_request)
        api_error = AnthropicStatusError(
            message="Internal server error",
            response=mock_response,
            body=None,
        )

        async def failing_stream(req):
            yield RawMessageStartEvent(
                type="message_start",
                message={
                    "id": "msg_123",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": "claude-3-5-sonnet-20241022",
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            )
            raise api_error

        mock_client = MagicMock()
        mock_client.stream = failing_stream

        with patch("luthien_proxy.pipeline.anthropic_processor.tracer") as mock_tracer:
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            mock_root_span = MagicMock()

            response = await _handle_streaming(
                final_request=request,
                policy=mock_policy,
                policy_ctx=mock_policy_ctx,
                anthropic_client=mock_client,
                call_id="test-call-id",
                root_span=mock_root_span,
            )

            # Collect all events from the stream
            events = []
            async for chunk in response.body_iterator:
                events.append(chunk)

        # Verify we got the initial event plus an error event
        assert len(events) >= 2  # message_start, error
        last_event = events[-1]
        assert "event: error" in last_event
        assert '"type": "api_error"' in last_event
        assert "Internal server error" in last_event

    @pytest.mark.asyncio
    async def test_mid_stream_connection_error_emits_error_event(self, mock_policy, mock_policy_ctx):
        """Test that connection errors mid-stream emit an error event."""
        request: AnthropicRequest = {
            "model": DEFAULT_CLAUDE_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 1024,
            "stream": True,
        }

        mock_request = HttpxRequest("POST", "https://api.anthropic.com/v1/messages")
        connection_error = AnthropicConnectionError(request=mock_request)

        async def failing_stream(req):
            yield RawMessageStartEvent(
                type="message_start",
                message={
                    "id": "msg_123",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": "claude-3-5-sonnet-20241022",
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            )
            raise connection_error

        mock_client = MagicMock()
        mock_client.stream = failing_stream

        with patch("luthien_proxy.pipeline.anthropic_processor.tracer") as mock_tracer:
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            mock_root_span = MagicMock()

            response = await _handle_streaming(
                final_request=request,
                policy=mock_policy,
                policy_ctx=mock_policy_ctx,
                anthropic_client=mock_client,
                call_id="test-call-id",
                root_span=mock_root_span,
            )

            events = []
            async for chunk in response.body_iterator:
                events.append(chunk)

        last_event = events[-1]
        assert "event: error" in last_event
        assert '"type": "api_connection_error"' in last_event
