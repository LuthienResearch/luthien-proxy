"""Unit tests for DebugLoggingPolicy.

Tests cover:
1. Policy name property
2. on_request logging behavior
3. on_chunk_received logging and passthrough
4. on_response passthrough
"""

from __future__ import annotations

import asyncio
from unittest.mock import Mock, patch

import pytest
from litellm.types.utils import ModelResponse

from luthien_proxy.llm.types import Request
from luthien_proxy.policies import PolicyContext
from luthien_proxy.policies.debug_logging_policy import DebugLoggingPolicy
from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext
from luthien_proxy.streaming.stream_state import StreamState
from luthien_proxy.types import RawHttpRequest


def create_mock_policy_context(
    raw_http_request: RawHttpRequest | None = None,
) -> PolicyContext:
    """Create a mock PolicyContext for testing."""
    ctx = Mock(spec=PolicyContext)
    ctx.transaction_id = "test-transaction-id"
    ctx.raw_http_request = raw_http_request
    ctx.scratchpad = {}
    ctx.record_event = Mock()
    return ctx


def create_mock_streaming_context(
    raw_chunks: list[ModelResponse] | None = None,
) -> StreamingPolicyContext:
    """Create a mock StreamingPolicyContext for testing."""
    ctx = Mock(spec=StreamingPolicyContext)

    # Create stream state
    ctx.original_streaming_response_state = StreamState()
    ctx.original_streaming_response_state.raw_chunks = raw_chunks or []

    # Egress queue
    ctx.egress_queue = asyncio.Queue()
    ctx.observability = Mock()

    return ctx


class TestDebugLoggingPolicyProperties:
    """Test DebugLoggingPolicy properties."""

    def test_short_policy_name(self):
        """Test short_policy_name property."""
        policy = DebugLoggingPolicy()
        assert policy.short_policy_name == "DebugLogging"

    def test_inherits_from_base_policy(self):
        """Test that DebugLoggingPolicy inherits from BasePolicy."""
        from luthien_proxy.policies.base_policy import BasePolicy

        policy = DebugLoggingPolicy()
        assert isinstance(policy, BasePolicy)


class TestDebugLoggingPolicyOnRequest:
    """Test on_request method."""

    @pytest.mark.asyncio
    async def test_on_request_with_raw_http_request(self):
        """Test on_request logs raw HTTP request when available."""
        policy = DebugLoggingPolicy()

        raw_request = RawHttpRequest(
            method="POST",
            path="/v1/chat/completions",
            headers={"Content-Type": "application/json"},
            body={"model": "gpt-4", "messages": []},
        )

        request = Request(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
        )

        context = create_mock_policy_context(raw_http_request=raw_request)

        with patch("luthien_proxy.policies.debug_logging_policy.logger") as mock_logger:
            result = await policy.on_request(request, context)

        # Should return request unchanged
        assert result == request

        # Should have logged
        assert mock_logger.info.call_count >= 3  # method, headers, body

        # Should have recorded event
        context.record_event.assert_called_once()
        call_args = context.record_event.call_args
        assert call_args[0][0] == "debug.raw_http_request"
        assert call_args[0][1]["method"] == "POST"
        assert call_args[0][1]["path"] == "/v1/chat/completions"

    @pytest.mark.asyncio
    async def test_on_request_without_raw_http_request(self):
        """Test on_request warns when raw HTTP request is not available."""
        policy = DebugLoggingPolicy()

        request = Request(
            model="gpt-4",
            messages=[{"role": "user", "content": "Hello"}],
        )

        context = create_mock_policy_context(raw_http_request=None)

        with patch("luthien_proxy.policies.debug_logging_policy.logger") as mock_logger:
            result = await policy.on_request(request, context)

        # Should return request unchanged
        assert result == request

        # Should have warned
        mock_logger.warning.assert_called_once()
        assert "No raw HTTP request" in mock_logger.warning.call_args[0][0]

        # Should not have recorded event
        context.record_event.assert_not_called()


class TestDebugLoggingPolicyOnChunkReceived:
    """Test on_chunk_received method."""

    @pytest.mark.asyncio
    async def test_on_chunk_received_logs_and_passes_through(self):
        """Test on_chunk_received logs chunk and passes it through."""
        policy = DebugLoggingPolicy()

        chunk = ModelResponse(
            id="test-chunk",
            object="chat.completion.chunk",
            created=123456,
            model="gpt-4",
            choices=[
                {
                    "index": 0,
                    "delta": {"content": "Hello"},
                    "finish_reason": None,
                }
            ],
        )

        ctx = create_mock_streaming_context(raw_chunks=[chunk])

        with patch("luthien_proxy.policies.debug_logging_policy.logger") as mock_logger:
            await policy.on_chunk_received(ctx)

        # Should have logged the chunk
        mock_logger.info.assert_called()
        log_message = mock_logger.info.call_args_list[0][0][0]
        assert "[CHUNK]" in log_message

        # Should have passed chunk through to egress queue
        assert not ctx.egress_queue.empty()
        passed_chunk = ctx.egress_queue.get_nowait()
        assert passed_chunk.id == "test-chunk"

    @pytest.mark.asyncio
    async def test_on_chunk_received_logs_hidden_params(self):
        """Test on_chunk_received logs hidden params if they exist."""
        policy = DebugLoggingPolicy()

        chunk = ModelResponse(
            id="test-chunk",
            object="chat.completion.chunk",
            created=123456,
            model="gpt-4",
            choices=[
                {
                    "index": 0,
                    "delta": {"content": "Hello"},
                    "finish_reason": None,
                }
            ],
        )
        # Add hidden params
        chunk._hidden_params = {"custom_key": "custom_value"}

        ctx = create_mock_streaming_context(raw_chunks=[chunk])

        with patch("luthien_proxy.policies.debug_logging_policy.logger") as mock_logger:
            await policy.on_chunk_received(ctx)

        # Should have logged hidden params
        log_calls = [str(call) for call in mock_logger.info.call_args_list]
        assert any("[HIDDEN_PARAMS]" in call for call in log_calls)


class TestDebugLoggingPolicyOnResponse:
    """Test on_response method."""

    @pytest.mark.asyncio
    async def test_on_response_passes_through(self, make_model_response):
        """Test on_response passes response through unchanged."""
        policy = DebugLoggingPolicy()

        response = make_model_response(content="Hello from assistant")
        context = Mock()

        result = await policy.on_response(response, context)

        # Should return response unchanged
        assert result == response
        assert result.choices[0].message.content == "Hello from assistant"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
