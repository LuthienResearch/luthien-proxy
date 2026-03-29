"""Unit tests for the Anthropic-native pipeline processor module."""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anthropic import APIConnectionError as AnthropicConnectionError
from anthropic import APIStatusError as AnthropicStatusError
from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    RawMessageStartEvent,
    RawMessageStopEvent,
    TextDelta,
)
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse as FastAPIStreamingResponse
from httpx import Request as HttpxRequest
from httpx import Response as HttpxResponse
from tests.constants import DEFAULT_TEST_MODEL

from luthien_proxy.exceptions import BackendAPIError
from luthien_proxy.llm.types.anthropic import AnthropicRequest, AnthropicResponse
from luthien_proxy.pipeline.anthropic_processor import (
    _build_error_event,
    _format_sse_event,
    _handle_anthropic_error,
    _process_request,
    _reconstruct_response_from_stream_events,
    _run_policy_hooks,
    process_anthropic_request,
)
from luthien_proxy.policies.noop_policy import NoOpPolicy
from luthien_proxy.policy_core.anthropic_execution_interface import (
    AnthropicPolicyEmission,
)
from luthien_proxy.policy_core.policy_context import PolicyContext


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
                "model": DEFAULT_TEST_MODEL,
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
        request = MagicMock()
        request.headers = {}
        request.method = "POST"
        request.url = MagicMock()
        request.url.path = "/v1/messages"
        request.is_disconnected = AsyncMock(return_value=False)
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
            "model": DEFAULT_TEST_MODEL,
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

        assert anthropic_request["model"] == DEFAULT_TEST_MODEL
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
            "model": DEFAULT_TEST_MODEL,
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
    async def test_extracts_session_id_from_header_fallback(self, mock_request, mock_emitter, mock_span):
        anthropic_body = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
        }
        mock_request.json = AsyncMock(return_value=anthropic_body)
        mock_request.headers = {"x-session-id": "oauth-session-abc123"}

        with patch("luthien_proxy.pipeline.anthropic_processor.tracer") as mock_tracer:
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            _anthropic_request, _raw_http_request, session_id = await _process_request(
                request=mock_request,
                call_id="test-call-id",
                emitter=mock_emitter,
            )

        assert session_id == "oauth-session-abc123"

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
            "model": DEFAULT_TEST_MODEL,
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
            "model": DEFAULT_TEST_MODEL,
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


class TestAnthropicRequestFlow:
    """Tests for non-streaming and streaming flow via process_anthropic_request."""

    @pytest.fixture
    def mock_request(self):
        request = MagicMock()
        request.headers = {}
        request.method = "POST"
        request.url = MagicMock()
        request.url.path = "/v1/messages"
        request.is_disconnected = AsyncMock(return_value=False)
        return request

    @pytest.fixture
    def mock_anthropic_response(self) -> AnthropicResponse:
        return AnthropicResponse(
            id="msg_test123",
            type="message",
            role="assistant",
            content=[{"type": "text", "text": "Hello there!"}],
            model=DEFAULT_TEST_MODEL,
            stop_reason="end_turn",
            stop_sequence=None,
            usage={"input_tokens": 10, "output_tokens": 5},
        )

    @pytest.fixture
    def mock_anthropic_client(self, mock_anthropic_response):
        client = MagicMock()
        client.complete = AsyncMock(return_value=mock_anthropic_response)
        return client

    @pytest.fixture
    def mock_policy(self):
        return NoOpPolicy()

    @pytest.fixture
    def mock_emitter(self):
        return MagicMock()

    @pytest.mark.asyncio
    async def test_non_streaming_returns_json_response(
        self, mock_request, mock_anthropic_client, mock_policy, mock_emitter
    ):
        anthropic_body: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hi"}],
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
        assert response.headers.get("x-call-id")
        mock_anthropic_client.complete.assert_called_once_with(anthropic_body, extra_headers=None)

    @pytest.mark.asyncio
    async def test_emits_client_response_event(self, mock_request, mock_anthropic_client, mock_policy, mock_emitter):
        anthropic_body: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hi"}],
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

        event_types = [call[0][1] for call in mock_emitter.record.call_args_list]
        assert "pipeline.client_response" in event_types

    @pytest.mark.asyncio
    async def test_emits_transaction_response_recorded_event(
        self, mock_request, mock_anthropic_client, mock_policy, mock_emitter
    ):
        anthropic_body: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hi"}],
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

        event_types = [call[0][1] for call in mock_emitter.record.call_args_list]
        assert "transaction.non_streaming_response_recorded" in event_types

        for call in mock_emitter.record.call_args_list:
            if call[0][1] == "transaction.non_streaming_response_recorded":
                payload = call[0][2]
                assert "original_response" in payload
                assert "final_response" in payload
                assert payload["final_response"]["role"] == "assistant"
                break

    @pytest.mark.asyncio
    async def test_streaming_returns_streaming_response(
        self, mock_request, mock_anthropic_client, mock_policy, mock_emitter
    ):
        anthropic_body: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 1024,
            "stream": True,
        }
        mock_request.json = AsyncMock(return_value=anthropic_body)

        async def mock_stream(request, extra_headers=None):
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

        mock_anthropic_client.stream = mock_stream

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

        assert isinstance(response, FastAPIStreamingResponse)
        assert response.media_type == "text/event-stream"
        assert response.headers.get("cache-control") == "no-cache"
        assert response.headers.get("x-call-id")


class TestProcessAnthropicRequest:
    """Integration tests for the main process_anthropic_request function."""

    @pytest.fixture
    def mock_request(self):
        request = MagicMock()
        request.headers = {}
        request.method = "POST"
        request.url = MagicMock()
        request.url.path = "/v1/messages"
        request.is_disconnected = AsyncMock(return_value=False)
        return request

    @pytest.fixture
    def mock_policy(self):
        return NoOpPolicy()

    @pytest.fixture
    def mock_anthropic_client(self):
        """Create a mock AnthropicClient."""
        response = AnthropicResponse(
            id="msg_test123",
            type="message",
            role="assistant",
            content=[{"type": "text", "text": "Hello!"}],
            model=DEFAULT_TEST_MODEL,
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
            "model": DEFAULT_TEST_MODEL,
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
    async def test_emits_transaction_request_recorded(
        self, mock_request, mock_policy, mock_anthropic_client, mock_emitter
    ):
        """Anthropic pipeline should emit transaction.request_recorded for history viewer."""
        anthropic_body = {
            "model": DEFAULT_TEST_MODEL,
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

        # Verify transaction.request_recorded was emitted
        event_types = [call[0][1] for call in mock_emitter.record.call_args_list]
        assert "transaction.request_recorded" in event_types

        # Verify payload structure
        for call in mock_emitter.record.call_args_list:
            if call[0][1] == "transaction.request_recorded":
                payload = call[0][2]
                assert payload["final_model"] == DEFAULT_TEST_MODEL
                assert "original_request" in payload
                assert "final_request" in payload
                assert payload["final_request"]["messages"][0]["content"] == "Hello"
                break

    @pytest.mark.asyncio
    async def test_streaming_request_returns_streaming_response(self, mock_request, mock_policy, mock_emitter):
        """Test streaming request returns StreamingResponse."""
        anthropic_body = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
            "stream": True,
        }
        mock_request.json = AsyncMock(return_value=anthropic_body)

        # Create streaming client
        async def mock_stream(request, extra_headers=None):
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
            "model": DEFAULT_TEST_MODEL,
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
            "model": DEFAULT_TEST_MODEL,
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
        assert event.get("error", {}).get("type") == "rate_limit_error"
        assert "Rate limit exceeded" in event.get("error", {}).get("message", "")

    def test_builds_connection_error_event(self):
        """Test building error event from AnthropicConnectionError."""
        mock_request = HttpxRequest("POST", "https://api.anthropic.com/v1/messages")
        error = AnthropicConnectionError(request=mock_request)

        event = _build_error_event(error, "test-call-id")

        assert event.get("type") == "error"
        assert event.get("error", {}).get("type") == "api_connection_error"

    def test_builds_generic_error_event(self):
        """Generic exceptions produce a sanitized error event — internal details are not forwarded."""
        error = RuntimeError("Something went wrong")

        event = _build_error_event(error, "test-call-id")

        assert event.get("type") == "error"
        assert event.get("error", {}).get("type") == "api_error"
        # Raw exception message must not leak to clients
        assert "Something went wrong" not in event.get("error", {}).get("message", "")


class TestMidStreamErrorHandling:
    """Tests for mid-stream error handling in streaming responses."""

    @pytest.fixture
    def mock_policy(self):
        """Create a mock Anthropic policy."""
        return NoOpPolicy()

    @pytest.mark.asyncio
    async def test_mid_stream_api_error_emits_error_event(self, mock_policy):
        """Test that API errors mid-stream emit an error event instead of raising."""
        anthropic_body: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
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

        async def failing_stream(req, extra_headers=None):
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

        mock_fastapi_request = MagicMock()
        mock_fastapi_request.headers = {}
        mock_fastapi_request.method = "POST"
        mock_fastapi_request.url = MagicMock()
        mock_fastapi_request.url.path = "/v1/messages"
        mock_fastapi_request.json = AsyncMock(return_value=anthropic_body)
        mock_fastapi_request.is_disconnected = AsyncMock(return_value=False)

        with patch("luthien_proxy.pipeline.anthropic_processor.tracer") as mock_tracer:
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            response = await process_anthropic_request(
                request=mock_fastapi_request,
                policy=mock_policy,
                anthropic_client=mock_client,
                emitter=MagicMock(),
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
    async def test_mid_stream_connection_error_emits_error_event(self, mock_policy):
        """Test that connection errors mid-stream emit an error event."""
        anthropic_body: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 1024,
            "stream": True,
        }

        mock_request = HttpxRequest("POST", "https://api.anthropic.com/v1/messages")
        connection_error = AnthropicConnectionError(request=mock_request)

        async def failing_stream(req, extra_headers=None):
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

        mock_fastapi_request = MagicMock()
        mock_fastapi_request.headers = {}
        mock_fastapi_request.method = "POST"
        mock_fastapi_request.url = MagicMock()
        mock_fastapi_request.url.path = "/v1/messages"
        mock_fastapi_request.json = AsyncMock(return_value=anthropic_body)
        mock_fastapi_request.is_disconnected = AsyncMock(return_value=False)

        with patch("luthien_proxy.pipeline.anthropic_processor.tracer") as mock_tracer:
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            response = await process_anthropic_request(
                request=mock_fastapi_request,
                policy=mock_policy,
                anthropic_client=mock_client,
                emitter=MagicMock(),
            )

            events = []
            async for chunk in response.body_iterator:
                events.append(chunk)

        last_event = events[-1]
        assert "event: error" in last_event
        assert '"type": "api_connection_error"' in last_event


class TestClientDisconnectHandling:
    @pytest.fixture
    def mock_policy(self):
        return NoOpPolicy()

    def _make_fastapi_request(self, is_disconnected: bool) -> MagicMock:
        mock_request = MagicMock()
        mock_request.headers = {}
        mock_request.method = "POST"
        mock_request.url = MagicMock()
        mock_request.url.path = "/v1/messages"
        mock_request.is_disconnected = AsyncMock(return_value=is_disconnected)
        return mock_request

    @pytest.mark.asyncio
    async def test_stream_yields_no_events_when_client_already_disconnected(self, mock_policy):
        anthropic_body: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 1024,
            "stream": True,
        }

        async def multi_event_stream(req, extra_headers=None):
            for i in range(5):
                yield RawMessageStartEvent(
                    type="message_start",
                    message={
                        "id": f"msg_{i}",
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                        "model": DEFAULT_TEST_MODEL,
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 10, "output_tokens": 0},
                    },
                )

        mock_client = MagicMock()
        mock_client.stream = multi_event_stream
        mock_fastapi_request = self._make_fastapi_request(is_disconnected=True)
        mock_fastapi_request.json = AsyncMock(return_value=anthropic_body)

        with patch("luthien_proxy.pipeline.anthropic_processor.tracer") as mock_tracer:
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            response = await process_anthropic_request(
                request=mock_fastapi_request,
                policy=mock_policy,
                anthropic_client=mock_client,
                emitter=MagicMock(),
            )

            events_yielded: list[str] = []
            async for chunk in response.body_iterator:
                events_yielded.append(chunk)

        assert len(events_yielded) == 0

    @pytest.mark.asyncio
    async def test_stream_delivers_events_when_client_connected(self, mock_policy):
        anthropic_body: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 1024,
            "stream": True,
        }

        async def single_event_stream(req, extra_headers=None):
            yield RawMessageStartEvent(
                type="message_start",
                message={
                    "id": "msg_abc",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": DEFAULT_TEST_MODEL,
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 10, "output_tokens": 0},
                },
            )

        mock_client = MagicMock()
        mock_client.stream = single_event_stream
        mock_fastapi_request = self._make_fastapi_request(is_disconnected=False)
        mock_fastapi_request.json = AsyncMock(return_value=anthropic_body)

        with patch("luthien_proxy.pipeline.anthropic_processor.tracer") as mock_tracer:
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            response = await process_anthropic_request(
                request=mock_fastapi_request,
                policy=mock_policy,
                anthropic_client=mock_client,
                emitter=MagicMock(),
            )

            events: list[str] = []
            async for chunk in response.body_iterator:
                events.append(chunk)

        assert len(events) == 1
        assert "message_start" in events[0]


class TestHandleAnthropicError:
    """Tests for _handle_anthropic_error error classification.

    Regression test: Previously raised HTTPException with nested JSON detail
    and generic 'api_error' type for all errors, making auth failures unclear.
    Now raises BackendAPIError with proper error types.
    """

    def test_auth_error_raises_backend_api_error(self):
        """401 AuthenticationError should raise BackendAPIError with authentication_error type."""
        mock_response = HttpxResponse(
            status_code=401,
            request=HttpxRequest("POST", "https://api.anthropic.com/v1/messages"),
            json={"error": {"type": "authentication_error", "message": "Invalid API Key"}},
        )
        exc = AnthropicStatusError(
            message="Invalid API Key",
            response=mock_response,
            body={"error": {"type": "authentication_error", "message": "Invalid API Key"}},
        )

        with pytest.raises(BackendAPIError) as exc_info:
            _handle_anthropic_error(exc, "test-call")

        assert exc_info.value.status_code == 401
        assert exc_info.value.error_type == "authentication_error"
        assert "Invalid API Key" in exc_info.value.message

    def test_rate_limit_error_raises_backend_api_error(self):
        """429 RateLimitError should raise BackendAPIError with rate_limit_error type."""
        mock_response = HttpxResponse(
            status_code=429,
            request=HttpxRequest("POST", "https://api.anthropic.com/v1/messages"),
            json={"error": {"type": "rate_limit_error", "message": "Rate limited"}},
        )
        exc = AnthropicStatusError(
            message="Rate limited",
            response=mock_response,
            body={"error": {"type": "rate_limit_error", "message": "Rate limited"}},
        )

        with pytest.raises(BackendAPIError) as exc_info:
            _handle_anthropic_error(exc, "test-call")

        assert exc_info.value.status_code == 429
        assert exc_info.value.error_type == "rate_limit_error"

    def test_connection_error_raises_backend_api_error(self):
        """Connection errors should raise BackendAPIError with 502."""
        exc = AnthropicConnectionError(request=HttpxRequest("POST", "https://api.anthropic.com/v1/messages"))

        with pytest.raises(BackendAPIError) as exc_info:
            _handle_anthropic_error(exc, "test-call")

        assert exc_info.value.status_code == 502
        assert exc_info.value.error_type == "api_connection_error"


class _InvalidStreamCompletePolicy:
    """Hook-based policy whose on_anthropic_stream_complete emits a full response dict (invalid for streaming)."""

    async def on_anthropic_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
        return request

    async def on_anthropic_response(self, response: AnthropicResponse, context: PolicyContext) -> AnthropicResponse:
        return response

    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: PolicyContext
    ) -> list[MessageStreamEvent]:
        return [event]

    async def on_anthropic_stream_complete(self, context: PolicyContext) -> list[AnthropicPolicyEmission]:
        return [
            {
                "id": "msg_invalid_streaming_emission",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "invalid streaming emission"}],
                "model": "claude-haiku-4-5",
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }
        ]


class _GenericErrorPolicy:
    """Hook-based policy that raises a generic (non-Anthropic) exception in on_anthropic_request."""

    async def on_anthropic_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
        raise RuntimeError("policy logic failed unexpectedly")

    async def on_anthropic_response(self, response: AnthropicResponse, context: PolicyContext) -> AnthropicResponse:
        return response

    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: PolicyContext
    ) -> list[MessageStreamEvent]:
        return [event]

    async def on_anthropic_stream_complete(self, context: PolicyContext) -> list[AnthropicPolicyEmission]:
        return []


class TestExecutionPolicyRuntime:
    """Tests for execution-oriented Anthropic policy runtime."""

    @pytest.fixture
    def mock_request(self):
        request = MagicMock()
        request.headers = {}
        request.method = "POST"
        request.url = MagicMock()
        request.url.path = "/v1/messages"
        request.json = AsyncMock(
            return_value={
                "model": DEFAULT_TEST_MODEL,
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 64,
                "stream": False,
            }
        )
        request.is_disconnected = AsyncMock(return_value=False)
        return request

    @pytest.fixture
    def mock_emitter(self):
        return MagicMock()

    @pytest.fixture
    def mock_anthropic_client(self):
        client = MagicMock()
        client.complete = AsyncMock()
        client.stream = MagicMock()
        return client

    @pytest.mark.asyncio
    async def test_non_streaming_policy_can_proxy_backend_complete(
        self,
        mock_request,
        mock_emitter,
        mock_anthropic_client,
    ):
        """Execution policy can call io.complete() and emit backend response."""
        backend_response: AnthropicResponse = {
            "id": "msg_backend_complete",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Backend complete response"}],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        mock_anthropic_client.complete = AsyncMock(return_value=backend_response)
        policy = NoOpPolicy()

        response = await process_anthropic_request(
            request=mock_request,
            policy=policy,
            anthropic_client=mock_anthropic_client,
            emitter=mock_emitter,
        )

        assert isinstance(response, JSONResponse)
        payload = response.body.decode()
        assert "msg_backend_complete" in payload
        assert "Backend complete response" in payload
        mock_anthropic_client.complete.assert_awaited_once()
        mock_anthropic_client.stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_streaming_policy_can_proxy_backend_stream(
        self,
        mock_request,
        mock_emitter,
        mock_anthropic_client,
    ):
        """Execution policy can call io.stream() and emit backend stream events."""
        mock_request.json = AsyncMock(
            return_value={
                "model": DEFAULT_TEST_MODEL,
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 64,
                "stream": True,
            }
        )

        async def backend_stream() -> AsyncIterator[RawMessageStartEvent | RawMessageStopEvent]:
            yield RawMessageStartEvent(
                type="message_start",
                message={
                    "id": "msg_backend_stream",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": DEFAULT_TEST_MODEL,
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 0},
                },
            )
            yield RawMessageStopEvent(type="message_stop")

        mock_anthropic_client.stream = MagicMock(return_value=backend_stream())
        policy = NoOpPolicy()

        response = await process_anthropic_request(
            request=mock_request,
            policy=policy,
            anthropic_client=mock_anthropic_client,
            emitter=mock_emitter,
        )

        assert isinstance(response, FastAPIStreamingResponse)
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)

        full_stream = "".join(chunks)
        assert "event: message_start" in full_stream
        assert "msg_backend_stream" in full_stream
        assert "event: message_stop" in full_stream
        mock_anthropic_client.stream.assert_called_once()
        mock_anthropic_client.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_streaming_policy_complete_error_raises_backend_api_error(
        self,
        mock_request,
        mock_emitter,
        mock_anthropic_client,
    ):
        """Errors raised by io.complete() should propagate as BackendAPIError."""
        mock_httpx_request = HttpxRequest("POST", "https://api.anthropic.com/v1/messages")
        mock_httpx_response = HttpxResponse(500, request=mock_httpx_request)
        mock_anthropic_client.complete = AsyncMock(
            side_effect=AnthropicStatusError(
                message="backend failed",
                response=mock_httpx_response,
                body={"error": {"type": "api_error", "message": "backend failed"}},
            )
        )
        policy = NoOpPolicy()

        with pytest.raises(BackendAPIError) as exc_info:
            await process_anthropic_request(
                request=mock_request,
                policy=policy,
                anthropic_client=mock_anthropic_client,
                emitter=mock_emitter,
            )

        assert exc_info.value.status_code == 500
        assert exc_info.value.error_type == "api_error"

    @pytest.mark.asyncio
    async def test_non_streaming_generic_policy_exception_returns_500(
        self,
        mock_request,
        mock_emitter,
        mock_anthropic_client,
    ):
        """Generic (non-Anthropic) policy exceptions in non-streaming mode raise BackendAPIError(500).

        Previously, non-Anthropic exceptions propagated as raw exceptions, potentially
        leaking internal details to the client. Now they are always caught and converted
        to a safe 500 BackendAPIError.
        """
        policy = _GenericErrorPolicy()

        with pytest.raises(BackendAPIError) as exc_info:
            await process_anthropic_request(
                request=mock_request,
                policy=policy,
                anthropic_client=mock_anthropic_client,
                emitter=mock_emitter,
            )

        assert exc_info.value.status_code == 500
        assert exc_info.value.error_type == "api_error"
        # Internal details must not be exposed
        assert "policy logic failed" not in exc_info.value.message

    @pytest.mark.asyncio
    async def test_streaming_generic_policy_exception_sanitizes_error_event(
        self,
        mock_request,
        mock_emitter,
        mock_anthropic_client,
    ):
        """Generic policy exceptions in streaming mode emit a sanitized SSE error event.

        Raw exception messages must not be forwarded to the client — internal details
        (stack traces, connection strings, etc.) should only appear in server logs.
        """
        mock_request.json = AsyncMock(
            return_value={
                "model": DEFAULT_TEST_MODEL,
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 64,
                "stream": True,
            }
        )
        policy = _GenericErrorPolicy()

        response = await process_anthropic_request(
            request=mock_request,
            policy=policy,
            anthropic_client=mock_anthropic_client,
            emitter=mock_emitter,
        )

        assert isinstance(response, FastAPIStreamingResponse)
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        full_stream = "".join(chunks)

        assert "event: error" in full_stream
        # Internal exception message must not reach the client
        assert "policy logic failed" not in full_stream

    @pytest.mark.asyncio
    async def test_streaming_policy_emitting_full_response_yields_error_event(
        self,
        mock_request,
        mock_emitter,
        mock_anthropic_client,
    ):
        """Policy emitting response objects via on_anthropic_stream_complete should produce error event."""
        mock_request.json = AsyncMock(
            return_value={
                "model": DEFAULT_TEST_MODEL,
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 64,
                "stream": True,
            }
        )

        async def _backend_stream():
            yield RawMessageStartEvent(
                type="message_start",
                message={
                    "id": "msg_test",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": DEFAULT_TEST_MODEL,
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 0},
                },
            )
            yield RawMessageStopEvent(type="message_stop")

        mock_anthropic_client.stream = MagicMock(return_value=_backend_stream())
        policy = _InvalidStreamCompletePolicy()

        response = await process_anthropic_request(
            request=mock_request,
            policy=policy,
            anthropic_client=mock_anthropic_client,
            emitter=mock_emitter,
        )

        assert isinstance(response, FastAPIStreamingResponse)
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        full_stream = "".join(chunks)
        assert "event: error" in full_stream
        # Internal error details must not leak to clients
        assert "must emit streaming events" not in full_stream

    @pytest.mark.asyncio
    async def test_anthropic_beta_header_forwarded_to_upstream_client(
        self,
        mock_emitter,
        mock_anthropic_client,
    ):
        """anthropic-beta header from the client request is forwarded to the upstream API call."""
        beta_value = "prompt-caching-2024-07-31"
        mock_request = MagicMock()
        mock_request.headers = {"anthropic-beta": beta_value}
        mock_request.method = "POST"
        mock_request.url = MagicMock()
        mock_request.url.path = "/v1/messages"
        mock_request.json = AsyncMock(
            return_value={
                "model": DEFAULT_TEST_MODEL,
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 64,
                "stream": False,
            }
        )

        backend_response: AnthropicResponse = {
            "id": "msg_beta_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "ok"}],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 5, "output_tokens": 1},
        }
        mock_anthropic_client.complete = AsyncMock(return_value=backend_response)
        policy = NoOpPolicy()

        await process_anthropic_request(
            request=mock_request,
            policy=policy,
            anthropic_client=mock_anthropic_client,
            emitter=mock_emitter,
        )

        mock_anthropic_client.complete.assert_awaited_once()
        call_kwargs = mock_anthropic_client.complete.call_args.kwargs
        assert call_kwargs.get("extra_headers") == {"anthropic-beta": beta_value}


class TestReconstructResponseFromStreamEvents:
    """Tests for _reconstruct_response_from_stream_events."""

    def _message_start(
        self, message_id: str = "msg_abc", model: str = DEFAULT_TEST_MODEL, input_tokens: int = 10
    ) -> RawMessageStartEvent:
        return RawMessageStartEvent(
            type="message_start",
            message={
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": model,
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": input_tokens, "output_tokens": 0},
            },
        )

    def test_reconstructs_simple_text_response(self):
        """Typical text streaming response is correctly reconstructed."""
        events = [
            self._message_start("msg_abc123", input_tokens=10),
            RawContentBlockStartEvent(type="content_block_start", index=0, content_block={"type": "text", "text": ""}),
            RawContentBlockDeltaEvent(
                type="content_block_delta", index=0, delta=TextDelta(type="text_delta", text="Bucharest")
            ),
            RawContentBlockDeltaEvent(
                type="content_block_delta", index=0, delta=TextDelta(type="text_delta", text=" is the capital.")
            ),
            RawContentBlockStopEvent(type="content_block_stop", index=0),
            RawMessageDeltaEvent(
                type="message_delta",
                delta={"stop_reason": "end_turn", "stop_sequence": None},
                usage={"output_tokens": 5},
            ),
            RawMessageStopEvent(type="message_stop"),
        ]

        result = _reconstruct_response_from_stream_events(events)

        assert result is not None
        assert result["id"] == "msg_abc123"
        assert result["model"] == DEFAULT_TEST_MODEL
        assert result["role"] == "assistant"
        assert result["stop_reason"] == "end_turn"
        assert result["usage"]["input_tokens"] == 10
        assert result["usage"]["output_tokens"] == 5
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        assert result["content"][0]["text"] == "Bucharest is the capital."

    def test_concatenates_multiple_text_deltas(self):
        """Multiple content_block_delta events for the same block are concatenated."""
        events = [
            self._message_start(),
            RawContentBlockStartEvent(type="content_block_start", index=0, content_block={"type": "text", "text": ""}),
            RawContentBlockDeltaEvent(
                type="content_block_delta", index=0, delta=TextDelta(type="text_delta", text="Hello")
            ),
            RawContentBlockDeltaEvent(
                type="content_block_delta", index=0, delta=TextDelta(type="text_delta", text=", ")
            ),
            RawContentBlockDeltaEvent(
                type="content_block_delta", index=0, delta=TextDelta(type="text_delta", text="world")
            ),
            RawContentBlockStopEvent(type="content_block_stop", index=0),
            RawMessageStopEvent(type="message_stop"),
        ]

        result = _reconstruct_response_from_stream_events(events)

        assert result is not None
        assert result["content"][0]["text"] == "Hello, world"

    def test_returns_none_without_message_start(self):
        """Returns None when stream lacked a message_start event (e.g., errored early)."""
        events = [RawMessageStopEvent(type="message_stop")]

        result = _reconstruct_response_from_stream_events(events)

        assert result is None

    def test_empty_events_returns_none(self):
        """Returns None for an empty event list."""
        assert _reconstruct_response_from_stream_events([]) is None


class TestStreamingResponseRecording:
    """Tests that streaming responses are saved to conversation_events."""

    @pytest.fixture
    def mock_request(self):
        request = MagicMock()
        request.headers = {}
        request.method = "POST"
        request.url = MagicMock()
        request.url.path = "/v1/messages"
        request.json = AsyncMock(
            return_value={
                "model": DEFAULT_TEST_MODEL,
                "messages": [{"role": "user", "content": "Ce a zis vulpea?"}],
                "max_tokens": 64,
                "stream": True,
            }
        )
        request.is_disconnected = AsyncMock(return_value=False)
        return request

    @pytest.fixture
    def mock_emitter(self):
        return MagicMock()

    @pytest.mark.asyncio
    async def test_streaming_response_emits_recorded_event(self, mock_request, mock_emitter):
        """Consuming a streaming response triggers transaction.streaming_response_recorded."""

        async def _backend_stream():
            yield RawMessageStartEvent(
                type="message_start",
                message={
                    "id": "msg_ring_ding",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": DEFAULT_TEST_MODEL,
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 8, "output_tokens": 0},
                },
            )
            yield RawContentBlockStartEvent(
                type="content_block_start", index=0, content_block={"type": "text", "text": ""}
            )
            yield RawContentBlockDeltaEvent(
                type="content_block_delta",
                index=0,
                delta=TextDelta(type="text_delta", text="Ring-ding-ding!"),
            )
            yield RawContentBlockStopEvent(type="content_block_stop", index=0)
            yield RawMessageDeltaEvent(
                type="message_delta",
                delta={"stop_reason": "end_turn", "stop_sequence": None},
                usage={"output_tokens": 4},
            )
            yield RawMessageStopEvent(type="message_stop")

        mock_client = MagicMock()
        mock_client.complete = AsyncMock()
        mock_client.stream = MagicMock(return_value=_backend_stream())

        response = await process_anthropic_request(
            request=mock_request,
            policy=NoOpPolicy(),
            anthropic_client=mock_client,
            emitter=mock_emitter,
        )

        assert isinstance(response, FastAPIStreamingResponse)
        async for _ in response.body_iterator:
            pass

        event_types = [call.args[1] for call in mock_emitter.record.call_args_list]
        assert "transaction.streaming_response_recorded" in event_types

        recorded_call = next(
            call
            for call in mock_emitter.record.call_args_list
            if call.args[1] == "transaction.streaming_response_recorded"
        )
        payload = recorded_call.args[2]
        assert payload["final_response"]["id"] == "msg_ring_ding"
        assert payload["final_response"]["content"][0]["text"] == "Ring-ding-ding!"


class _StubIO:
    """Minimal IO stub for testing _run_policy_hooks directly."""

    def __init__(self, request: AnthropicRequest, response: AnthropicResponse | None = None, stream_events=None):
        self._request = request
        self._response = response
        self._stream_events = stream_events or []

    @property
    def request(self) -> AnthropicRequest:
        return self._request

    def set_request(self, request: AnthropicRequest) -> None:
        self._request = request

    @property
    def first_backend_response(self) -> AnthropicResponse | None:
        return self._response

    async def complete(self, request: AnthropicRequest | None = None) -> AnthropicResponse:
        assert self._response is not None
        return self._response

    async def stream(self, request: AnthropicRequest | None = None):
        for event in self._stream_events:
            yield event


class TestRunPolicyHooks:
    """Focused tests for _run_policy_hooks execution loop."""

    @pytest.mark.asyncio
    async def test_non_streaming_calls_request_and_response_hooks(self):
        """Non-streaming: on_anthropic_request then on_anthropic_response."""
        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 10,
            "stream": False,
        }
        response: AnthropicResponse = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "hello"}],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        io = _StubIO(request=request, response=response)
        ctx = PolicyContext.for_testing()

        emissions = []
        async for emission in _run_policy_hooks(NoOpPolicy(), io, ctx):
            emissions.append(emission)

        assert len(emissions) == 1
        assert emissions[0]["id"] == "msg_test"

    @pytest.mark.asyncio
    async def test_streaming_calls_stream_event_and_complete_hooks(self):
        """Streaming: on_anthropic_request, then stream events, then on_anthropic_stream_complete."""
        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 10,
            "stream": True,
        }
        stream_events = [
            RawMessageStartEvent(
                type="message_start",
                message={
                    "id": "msg_stream",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": DEFAULT_TEST_MODEL,
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 0},
                },
            ),
            RawMessageStopEvent(type="message_stop"),
        ]
        io = _StubIO(request=request, stream_events=stream_events)
        ctx = PolicyContext.for_testing()

        emissions = []
        async for emission in _run_policy_hooks(NoOpPolicy(), io, ctx):
            emissions.append(emission)

        assert len(emissions) == 2
        assert isinstance(emissions[0], RawMessageStartEvent)
        assert isinstance(emissions[1], RawMessageStopEvent)

    @pytest.mark.asyncio
    async def test_request_hook_transforms_are_applied(self):
        """Verify on_anthropic_request can modify the request before backend call."""

        class _AddMaxTokensPolicy:
            async def on_anthropic_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
                request["max_tokens"] = 999
                return request

            async def on_anthropic_response(
                self, response: AnthropicResponse, context: PolicyContext
            ) -> AnthropicResponse:
                return response

            async def on_anthropic_stream_event(
                self, event: MessageStreamEvent, context: PolicyContext
            ) -> list[MessageStreamEvent]:
                return [event]

            async def on_anthropic_stream_complete(self, context: PolicyContext) -> list[AnthropicPolicyEmission]:
                return []

        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 10,
            "stream": False,
        }
        response: AnthropicResponse = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "ok"}],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        io = _StubIO(request=request, response=response)
        ctx = PolicyContext.for_testing()

        async for _ in _run_policy_hooks(_AddMaxTokensPolicy(), io, ctx):
            pass

        assert io.request["max_tokens"] == 999

    @pytest.mark.asyncio
    async def test_stream_complete_emissions_are_appended(self):
        """on_anthropic_stream_complete events are yielded after stream events."""

        class _AppendPolicy:
            async def on_anthropic_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
                return request

            async def on_anthropic_response(
                self, response: AnthropicResponse, context: PolicyContext
            ) -> AnthropicResponse:
                return response

            async def on_anthropic_stream_event(
                self, event: MessageStreamEvent, context: PolicyContext
            ) -> list[MessageStreamEvent]:
                return [event]

            async def on_anthropic_stream_complete(self, context: PolicyContext) -> list[AnthropicPolicyEmission]:
                return [RawMessageStopEvent(type="message_stop")]

        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 10,
            "stream": True,
        }
        stream_events = [
            RawMessageStartEvent(
                type="message_start",
                message={
                    "id": "msg_s",
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": DEFAULT_TEST_MODEL,
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 0},
                },
            ),
        ]
        io = _StubIO(request=request, stream_events=stream_events)
        ctx = PolicyContext.for_testing()

        emissions = []
        async for emission in _run_policy_hooks(_AppendPolicy(), io, ctx):
            emissions.append(emission)

        assert len(emissions) == 2
        assert isinstance(emissions[0], RawMessageStartEvent)
        assert isinstance(emissions[1], RawMessageStopEvent)

    @pytest.mark.asyncio
    async def test_multi_serial_policy_ordering_through_hooks(self):
        """MultiSerialPolicy chains hooks in list order through _run_policy_hooks.

        Replaces the deleted TestMultiSerialAnthropicRunOrdering — verifies that
        [StringReplacement, AllCaps] produces the correct order: replace first,
        then uppercase.
        """
        from luthien_proxy.policies.all_caps_policy import AllCapsPolicy
        from luthien_proxy.policies.multi_serial_policy import MultiSerialPolicy
        from luthien_proxy.policies.string_replacement_policy import StringReplacementPolicy

        replacement = StringReplacementPolicy(config={"replacements": [["hello", "goodbye"]]})
        allcaps = AllCapsPolicy()
        pipeline = MultiSerialPolicy.from_instances([replacement, allcaps])

        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 10,
            "stream": False,
        }
        response: AnthropicResponse = {
            "id": "msg_order",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "hello world"}],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 2},
        }
        io = _StubIO(request=request, response=response)
        ctx = PolicyContext.for_testing()

        emissions = []
        async for emission in _run_policy_hooks(pipeline, io, ctx):
            emissions.append(emission)

        assert len(emissions) == 1
        assert emissions[0]["content"][0]["text"] == "GOODBYE WORLD"
