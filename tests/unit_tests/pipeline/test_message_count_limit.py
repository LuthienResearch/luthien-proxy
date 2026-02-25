"""Unit tests for message count limit validation.

Tests that both OpenAI and Anthropic request pipelines reject requests
with too many messages, while allowing normal-sized requests through.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from luthien_proxy.pipeline.anthropic_processor import (
    _process_request as anthropic_process_request,
)
from luthien_proxy.pipeline.processor import (
    _process_request as openai_process_request,
)
from luthien_proxy.utils.constants import MAX_MESSAGE_COUNT


class TestOpenAIMessageCountLimit:
    """Tests for message count validation in the OpenAI pipeline."""

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
    async def test_rejects_request_exceeding_message_count_limit(self, mock_request, mock_emitter, mock_span):
        """Request with more messages than MAX_MESSAGE_COUNT should be rejected with 400."""
        messages = [{"role": "user", "content": f"Message {i}"} for i in range(MAX_MESSAGE_COUNT + 1)]
        body = {
            "model": "gpt-4",
            "messages": messages,
        }
        mock_request.json = AsyncMock(return_value=body)

        with patch("luthien_proxy.pipeline.processor.tracer") as mock_tracer:
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(HTTPException) as exc_info:
                await openai_process_request(
                    request=mock_request,
                    call_id="test-call-id",
                    emitter=mock_emitter,
                )

        assert exc_info.value.status_code == 400
        assert str(MAX_MESSAGE_COUNT) in exc_info.value.detail
        assert "messages" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_allows_request_at_message_count_limit(self, mock_request, mock_emitter, mock_span):
        """Request with exactly MAX_MESSAGE_COUNT messages should be allowed."""
        messages = [{"role": "user", "content": f"Message {i}"} for i in range(MAX_MESSAGE_COUNT)]
        body = {
            "model": "gpt-4",
            "messages": messages,
        }
        mock_request.json = AsyncMock(return_value=body)

        with patch("luthien_proxy.pipeline.processor.tracer") as mock_tracer:
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            request_message, _raw_http_request, _session_id = await openai_process_request(
                request=mock_request,
                call_id="test-call-id",
                emitter=mock_emitter,
            )

        assert request_message.model == "gpt-4"

    @pytest.mark.asyncio
    async def test_allows_normal_message_count(self, mock_request, mock_emitter, mock_span):
        """Request with a small number of messages should pass through fine."""
        body = {
            "model": "gpt-4",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        mock_request.json = AsyncMock(return_value=body)

        with patch("luthien_proxy.pipeline.processor.tracer") as mock_tracer:
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            request_message, _raw_http_request, _session_id = await openai_process_request(
                request=mock_request,
                call_id="test-call-id",
                emitter=mock_emitter,
            )

        assert request_message.model == "gpt-4"


class TestAnthropicMessageCountLimit:
    """Tests for message count validation in the Anthropic pipeline."""

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
    async def test_rejects_request_exceeding_message_count_limit(self, mock_request, mock_emitter, mock_span):
        """Anthropic request with more messages than MAX_MESSAGE_COUNT should be rejected with 400."""
        messages = [{"role": "user", "content": f"Message {i}"} for i in range(MAX_MESSAGE_COUNT + 1)]
        body = {
            "model": "claude-sonnet-4-20250514",
            "messages": messages,
            "max_tokens": 1024,
        }
        mock_request.json = AsyncMock(return_value=body)

        with patch("luthien_proxy.pipeline.anthropic_processor.tracer") as mock_tracer:
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(HTTPException) as exc_info:
                await anthropic_process_request(
                    request=mock_request,
                    call_id="test-call-id",
                    emitter=mock_emitter,
                )

        assert exc_info.value.status_code == 400
        assert str(MAX_MESSAGE_COUNT) in exc_info.value.detail
        assert "messages" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_allows_request_at_message_count_limit(self, mock_request, mock_emitter, mock_span):
        """Anthropic request with exactly MAX_MESSAGE_COUNT messages should be allowed."""
        messages = [{"role": "user", "content": f"Message {i}"} for i in range(MAX_MESSAGE_COUNT)]
        body = {
            "model": "claude-sonnet-4-20250514",
            "messages": messages,
            "max_tokens": 1024,
        }
        mock_request.json = AsyncMock(return_value=body)

        with patch("luthien_proxy.pipeline.anthropic_processor.tracer") as mock_tracer:
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            anthropic_request, _raw_http_request, _session_id = await anthropic_process_request(
                request=mock_request,
                call_id="test-call-id",
                emitter=mock_emitter,
            )

        assert anthropic_request["model"] == "claude-sonnet-4-20250514"

    @pytest.mark.asyncio
    async def test_allows_normal_message_count(self, mock_request, mock_emitter, mock_span):
        """Anthropic request with a small number of messages should pass through fine."""
        body = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
        }
        mock_request.json = AsyncMock(return_value=body)

        with patch("luthien_proxy.pipeline.anthropic_processor.tracer") as mock_tracer:
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            anthropic_request, _raw_http_request, _session_id = await anthropic_process_request(
                request=mock_request,
                call_id="test-call-id",
                emitter=mock_emitter,
            )

        assert anthropic_request["model"] == "claude-sonnet-4-20250514"

    @pytest.mark.asyncio
    async def test_rejects_before_other_validation(self, mock_request, mock_emitter, mock_span):
        """Message count validation should happen after body parsing but catch oversized arrays.

        Even if the body is otherwise valid, too many messages should be rejected.
        """
        messages = [{"role": "user", "content": "x"} for _ in range(MAX_MESSAGE_COUNT + 100)]
        body = {
            "model": "claude-sonnet-4-20250514",
            "messages": messages,
            "max_tokens": 1024,
        }
        mock_request.json = AsyncMock(return_value=body)

        with patch("luthien_proxy.pipeline.anthropic_processor.tracer") as mock_tracer:
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(HTTPException) as exc_info:
                await anthropic_process_request(
                    request=mock_request,
                    call_id="test-call-id",
                    emitter=mock_emitter,
                )

        assert exc_info.value.status_code == 400
