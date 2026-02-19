"""Unit tests for the pipeline processor module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse as FastAPIStreamingResponse
from litellm.types.utils import Choices, Message, ModelResponse, Usage

from luthien_proxy.llm.types import Request as RequestMessage
from luthien_proxy.pipeline.processor import (
    _get_client_formatter,
    _handle_non_streaming,
    _handle_streaming,
    _process_request,
    process_llm_request,
)
from luthien_proxy.policy_core.openai_interface import OpenAIPolicyInterface
from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext
from luthien_proxy.streaming.client_formatter.openai import OpenAIClientFormatter


class MockOpenAIPolicy(OpenAIPolicyInterface):
    """Mock policy implementing OpenAIPolicyInterface for testing."""

    @property
    def short_policy_name(self) -> str:
        return "MockOpenAI"

    async def on_openai_request(self, request, context):
        return request

    async def on_openai_response(self, response, context):
        return response

    async def on_chunk_received(self, ctx: StreamingPolicyContext) -> None:
        pass

    async def on_content_delta(self, ctx: StreamingPolicyContext) -> None:
        pass

    async def on_content_complete(self, ctx: StreamingPolicyContext) -> None:
        pass

    async def on_tool_call_delta(self, ctx: StreamingPolicyContext) -> None:
        pass

    async def on_tool_call_complete(self, ctx: StreamingPolicyContext) -> None:
        pass

    async def on_finish_reason(self, ctx: StreamingPolicyContext) -> None:
        pass

    async def on_stream_complete(self, ctx: StreamingPolicyContext) -> None:
        pass

    async def on_streaming_policy_complete(self, ctx: StreamingPolicyContext) -> None:
        pass


class TestGetClientFormatter:
    """Tests for _get_client_formatter helper function."""

    def test_returns_openai_formatter(self):
        """Test returns OpenAIClientFormatter with correct model name."""
        formatter = _get_client_formatter("gpt-4")
        assert isinstance(formatter, OpenAIClientFormatter)
        assert formatter.model_name == "gpt-4"

    def test_model_name_passed_correctly(self):
        """Test model name is correctly passed to formatter."""
        model_name = "custom-model-v1"
        formatter = _get_client_formatter(model_name)
        assert formatter.model_name == model_name


class TestProcessRequest:
    """Tests for _process_request helper function."""

    @pytest.fixture
    def mock_request(self):
        """Create a mock FastAPI request."""
        request = MagicMock()
        request.headers = {}
        request.method = "POST"
        request.url = MagicMock()
        request.url.path = "/v1/chat/completions"
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
    async def test_openai_request_parsing(self, mock_request, mock_emitter, mock_span):
        """Test parsing a valid OpenAI format request."""
        openai_body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        }
        mock_request.json = AsyncMock(return_value=openai_body)

        with patch("luthien_proxy.pipeline.processor.tracer") as mock_tracer:
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            request_message, raw_http_request, session_id = await _process_request(
                request=mock_request,
                call_id="test-call-id",
                emitter=mock_emitter,
            )

        assert request_message.model == "gpt-4"
        assert request_message.stream is False
        assert raw_http_request.body == openai_body
        assert raw_http_request.method == "POST"
        assert raw_http_request.path == "/v1/chat/completions"
        assert session_id is None  # No x-session-id header provided
        mock_emitter.record.assert_called()

    @pytest.mark.asyncio
    async def test_request_size_limit_exceeded(self, mock_request, mock_emitter, mock_span):
        """Test that oversized requests raise HTTPException."""
        mock_request.headers = {"content-length": "999999999"}
        mock_request.json = AsyncMock(return_value={})

        with patch("luthien_proxy.pipeline.processor.tracer") as mock_tracer:
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
    async def test_request_within_size_limit(self, mock_request, mock_emitter, mock_span):
        """Test that appropriately sized requests are processed."""
        mock_request.headers = {"content-length": "100"}
        openai_body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hi"}],
        }
        mock_request.json = AsyncMock(return_value=openai_body)

        with patch("luthien_proxy.pipeline.processor.tracer") as mock_tracer:
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            request_message, _raw_http_request, _session_id = await _process_request(
                request=mock_request,
                call_id="test-call-id",
                emitter=mock_emitter,
            )

        assert request_message.model == "gpt-4"


class TestHandleNonStreaming:
    """Tests for _handle_non_streaming helper function."""

    @pytest.fixture
    def mock_model_response(self):
        """Create a mock ModelResponse."""
        return ModelResponse(
            id="test-response-id",
            created=1234567890,
            model="gpt-4",
            object="chat.completion",
            choices=[
                Choices(
                    index=0,
                    message=Message(role="assistant", content="Hello!"),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

    @pytest.fixture
    def mock_orchestrator(self, mock_model_response):
        """Create a mock PolicyOrchestrator."""
        orchestrator = MagicMock()
        orchestrator.process_full_response = AsyncMock(return_value=mock_model_response)
        return orchestrator

    @pytest.fixture
    def mock_llm_client(self, mock_model_response):
        """Create a mock LLM client."""
        client = MagicMock()
        client.complete = AsyncMock(return_value=mock_model_response)
        return client

    @pytest.fixture
    def mock_policy_ctx(self):
        """Create a mock PolicyContext."""
        return MagicMock()

    @pytest.fixture
    def mock_emitter(self):
        """Create a mock event emitter."""
        return MagicMock()

    @pytest.mark.asyncio
    async def test_openai_non_streaming_response(
        self, mock_orchestrator, mock_llm_client, mock_policy_ctx, mock_emitter, mock_model_response
    ):
        """Test non-streaming response for OpenAI format."""
        request = RequestMessage(model="gpt-4", messages=[{"role": "user", "content": "Hi"}])

        with patch("luthien_proxy.pipeline.processor.tracer") as mock_tracer:
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            response = await _handle_non_streaming(
                final_request=request,
                orchestrator=mock_orchestrator,
                policy_ctx=mock_policy_ctx,
                llm_client=mock_llm_client,
                emitter=mock_emitter,
                call_id="test-call-id",
            )

        assert isinstance(response, JSONResponse)
        assert response.headers.get("x-call-id") == "test-call-id"
        mock_llm_client.complete.assert_called_once_with(request)
        mock_orchestrator.process_full_response.assert_called_once()


class TestHandleStreaming:
    """Tests for _handle_streaming helper function."""

    @pytest.fixture
    def mock_orchestrator(self):
        """Create a mock PolicyOrchestrator."""
        orchestrator = MagicMock()

        async def mock_process_streaming(*args):
            yield "data: test\n\n"

        orchestrator.process_streaming_response = mock_process_streaming
        return orchestrator

    @pytest.fixture
    def mock_llm_client(self):
        """Create a mock LLM client."""
        client = MagicMock()

        async def mock_stream(*args):
            yield MagicMock()

        client.stream = AsyncMock(return_value=mock_stream())
        return client

    @pytest.fixture
    def mock_policy_ctx(self):
        """Create a mock PolicyContext."""
        return MagicMock()

    @pytest.mark.asyncio
    async def test_streaming_response_returns_streaming_response(
        self, mock_orchestrator, mock_llm_client, mock_policy_ctx
    ):
        """Test streaming handler returns FastAPIStreamingResponse."""
        request = RequestMessage(model="gpt-4", messages=[{"role": "user", "content": "Hi"}], stream=True)

        with patch("luthien_proxy.pipeline.processor.tracer") as mock_tracer:
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            mock_root_span = MagicMock()

            response = await _handle_streaming(
                final_request=request,
                orchestrator=mock_orchestrator,
                policy_ctx=mock_policy_ctx,
                llm_client=mock_llm_client,
                call_id="test-call-id",
                root_span=mock_root_span,
            )

        assert isinstance(response, FastAPIStreamingResponse)
        assert response.media_type == "text/event-stream"
        assert response.headers.get("cache-control") == "no-cache"
        assert response.headers.get("x-call-id") == "test-call-id"


class TestProcessLlmRequest:
    """Integration tests for the main process_llm_request function."""

    @pytest.fixture
    def mock_request(self):
        """Create a mock FastAPI request."""
        request = MagicMock()
        request.headers = {}
        request.method = "POST"
        request.url = MagicMock()
        request.url.path = "/v1/chat/completions"
        return request

    @pytest.fixture
    def mock_policy(self):
        """Create a mock policy implementing OpenAIPolicyInterface."""
        return MockOpenAIPolicy()

    @pytest.fixture
    def mock_llm_client(self):
        """Create a mock LLM client."""
        response = ModelResponse(
            id="test-response-id",
            created=1234567890,
            model="gpt-4",
            object="chat.completion",
            choices=[
                Choices(
                    index=0,
                    message=Message(role="assistant", content="Hello!"),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )
        client = MagicMock()
        client.complete = AsyncMock(return_value=response)
        return client

    @pytest.fixture
    def mock_emitter(self):
        """Create a mock event emitter."""
        return MagicMock()

    @pytest.mark.asyncio
    async def test_non_streaming_openai_request(self, mock_request, mock_policy, mock_llm_client, mock_emitter):
        """Test processing a non-streaming OpenAI request end-to-end."""
        openai_body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        }
        mock_request.json = AsyncMock(return_value=openai_body)

        with patch("luthien_proxy.pipeline.processor.tracer") as mock_tracer:
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            response = await process_llm_request(
                request=mock_request,
                policy=mock_policy,
                llm_client=mock_llm_client,
                emitter=mock_emitter,
            )

        assert isinstance(response, JSONResponse)
        mock_llm_client.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_streaming_request_returns_streaming_response(
        self, mock_request, mock_policy, mock_llm_client, mock_emitter
    ):
        """Test streaming request returns StreamingResponse."""
        openai_body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
        }
        mock_request.json = AsyncMock(return_value=openai_body)

        async def mock_stream(*args):
            yield MagicMock()

        mock_llm_client.stream = AsyncMock(return_value=mock_stream())

        with patch("luthien_proxy.pipeline.processor.tracer") as mock_tracer:
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            response = await process_llm_request(
                request=mock_request,
                policy=mock_policy,
                llm_client=mock_llm_client,
                emitter=mock_emitter,
            )

        assert isinstance(response, FastAPIStreamingResponse)

    @pytest.mark.asyncio
    async def test_request_too_large_raises_413(self, mock_request, mock_policy, mock_llm_client, mock_emitter):
        """Test oversized request returns 413 error."""
        mock_request.headers = {"content-length": "999999999"}
        mock_request.json = AsyncMock(return_value={})

        with patch("luthien_proxy.pipeline.processor.tracer") as mock_tracer:
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(HTTPException) as exc_info:
                await process_llm_request(
                    request=mock_request,
                    policy=mock_policy,
                    llm_client=mock_llm_client,
                    emitter=mock_emitter,
                )

        assert exc_info.value.status_code == 413

    @pytest.mark.asyncio
    async def test_span_attributes_set_correctly(self, mock_request, mock_policy, mock_llm_client, mock_emitter):
        """Test that span attributes are set correctly."""
        openai_body = {
            "model": "gpt-4-turbo",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        }
        mock_request.json = AsyncMock(return_value=openai_body)

        with patch("luthien_proxy.pipeline.processor.tracer") as mock_tracer:
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            await process_llm_request(
                request=mock_request,
                policy=mock_policy,
                llm_client=mock_llm_client,
                emitter=mock_emitter,
            )

        # Check that span attributes were set
        set_attribute_calls = [call[0] for call in mock_span.set_attribute.call_args_list]
        attribute_names = [call[0] for call in set_attribute_calls]

        assert "luthien.transaction_id" in attribute_names
        assert "luthien.client_format" in attribute_names
        assert "luthien.model" in attribute_names
        assert "luthien.stream" in attribute_names

    @pytest.mark.asyncio
    async def test_endpoint_span_attribute_openai(self, mock_request, mock_policy, mock_llm_client, mock_emitter):
        """Test that luthien.endpoint span attribute is set for OpenAI format."""
        openai_body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        }
        mock_request.json = AsyncMock(return_value=openai_body)

        with patch("luthien_proxy.pipeline.processor.tracer") as mock_tracer:
            mock_span = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            await process_llm_request(
                request=mock_request,
                policy=mock_policy,
                llm_client=mock_llm_client,
                emitter=mock_emitter,
            )

        # Check that endpoint attribute was set to OpenAI endpoint
        mock_span.set_attribute.assert_any_call("luthien.endpoint", "/v1/chat/completions")


class TestProcessRequestErrorHandling:
    """Tests for error handling during request processing."""

    @pytest.fixture
    def mock_request(self):
        """Create a mock FastAPI request."""
        request = MagicMock()
        request.headers = {}
        request.method = "POST"
        request.url = MagicMock()
        request.url.path = "/v1/chat/completions"
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
    async def test_invalid_openai_request_returns_400(self, mock_request, mock_emitter, mock_span):
        """Test that invalid OpenAI request format returns 400 error."""
        # Invalid OpenAI request - missing required fields
        invalid_openai_body = {
            "not_a_valid_field": "invalid",
        }
        mock_request.json = AsyncMock(return_value=invalid_openai_body)

        with patch("luthien_proxy.pipeline.processor.tracer") as mock_tracer:
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(HTTPException) as exc_info:
                await _process_request(
                    request=mock_request,
                    call_id="test-call-id",
                    emitter=mock_emitter,
                )

        assert exc_info.value.status_code == 400
        assert "Invalid OpenAI request format" in exc_info.value.detail
