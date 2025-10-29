# ABOUTME: Unit tests for TransactionRecorder implementations
# ABOUTME: Tests NoOpTransactionRecorder and DefaultTransactionRecorder behavior

from unittest.mock import AsyncMock, Mock, patch

import pytest
from litellm.types.utils import Choices, Message, ModelResponse

from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.observability.context import ObservabilityContext
from luthien_proxy.v2.observability.transaction_recorder import (
    DefaultTransactionRecorder,
    NoOpTransactionRecorder,
)


class TestNoOpTransactionRecorder:
    """Test that NoOpTransactionRecorder implements all methods as no-ops."""

    @pytest.mark.asyncio
    async def test_record_request_does_nothing(self):
        """record_request does nothing and doesn't raise."""
        recorder = NoOpTransactionRecorder()
        original = Request(model="gpt-4", messages=[])
        final = Request(model="gpt-4-turbo", messages=[])
        await recorder.record_request(original, final)
        # No assertion - just verify it doesn't raise

    def test_add_ingress_chunk_does_nothing(self):
        """add_ingress_chunk does nothing and doesn't raise."""
        recorder = NoOpTransactionRecorder()
        chunk = Mock(spec=ModelResponse)
        recorder.add_ingress_chunk(chunk)
        # No assertion - just verify it doesn't raise

    def test_add_egress_chunk_does_nothing(self):
        """add_egress_chunk does nothing and doesn't raise."""
        recorder = NoOpTransactionRecorder()
        chunk = Mock(spec=ModelResponse)
        recorder.add_egress_chunk(chunk)
        # No assertion - just verify it doesn't raise

    @pytest.mark.asyncio
    async def test_finalize_streaming_does_nothing(self):
        """finalize_streaming does nothing and doesn't raise."""
        recorder = NoOpTransactionRecorder()
        await recorder.finalize_streaming()
        # No assertion - just verify it doesn't raise

    @pytest.mark.asyncio
    async def test_finalize_non_streaming_does_nothing(self):
        """finalize_non_streaming does nothing and doesn't raise."""
        recorder = NoOpTransactionRecorder()
        original = Mock(spec=ModelResponse)
        final = Mock(spec=ModelResponse)
        await recorder.finalize_non_streaming(original, final)
        # No assertion - just verify it doesn't raise


class TestDefaultTransactionRecorder:
    """Test DefaultTransactionRecorder emits events and buffers correctly."""

    @pytest.mark.asyncio
    async def test_record_request_emits_event(self):
        """record_request emits event with correct data."""
        observability = Mock(spec=ObservabilityContext)
        observability.emit_event = AsyncMock()
        recorder = DefaultTransactionRecorder(observability)

        original = Request(model="gpt-4", messages=[{"role": "user", "content": "hi"}])
        final = Request(model="gpt-4-turbo", messages=[{"role": "user", "content": "hello"}])

        await recorder.record_request(original, final)

        # Verify emit_event called with correct data
        observability.emit_event.assert_called_once()
        call_args = observability.emit_event.call_args
        assert call_args[1]["event_type"] == "transaction.request_recorded"
        assert call_args[1]["data"]["original_model"] == "gpt-4"
        assert call_args[1]["data"]["final_model"] == "gpt-4-turbo"
        assert call_args[1]["data"]["original_request"]["model"] == "gpt-4"
        assert call_args[1]["data"]["final_request"]["model"] == "gpt-4-turbo"

        # Verify span attributes added
        assert observability.add_span_attribute.call_count == 2
        observability.add_span_attribute.assert_any_call("request.model", "gpt-4-turbo")
        observability.add_span_attribute.assert_any_call("request.message_count", 1)

    def test_add_ingress_chunk_buffers(self):
        """add_ingress_chunk stores chunks in buffer."""
        observability = Mock(spec=ObservabilityContext)
        recorder = DefaultTransactionRecorder(observability)

        chunk1 = Mock(spec=ModelResponse)
        chunk2 = Mock(spec=ModelResponse)

        recorder.add_ingress_chunk(chunk1)
        recorder.add_ingress_chunk(chunk2)

        assert recorder.ingress_chunks == [chunk1, chunk2]

    def test_add_egress_chunk_buffers(self):
        """add_egress_chunk stores chunks in buffer."""
        observability = Mock(spec=ObservabilityContext)
        recorder = DefaultTransactionRecorder(observability)

        chunk1 = Mock(spec=ModelResponse)
        chunk2 = Mock(spec=ModelResponse)

        recorder.add_egress_chunk(chunk1)
        recorder.add_egress_chunk(chunk2)

        assert recorder.egress_chunks == [chunk1, chunk2]

    @pytest.mark.asyncio
    async def test_finalize_streaming_reconstructs_and_emits(self):
        """finalize_streaming reconstructs responses and emits event."""
        observability = Mock(spec=ObservabilityContext)
        observability.emit_event = AsyncMock()
        recorder = DefaultTransactionRecorder(observability)

        # Add some chunks
        chunk1 = Mock(spec=ModelResponse)
        chunk2 = Mock(spec=ModelResponse)
        recorder.add_ingress_chunk(chunk1)
        recorder.add_egress_chunk(chunk2)

        with patch("luthien_proxy.v2.storage.events.reconstruct_full_response_from_chunks") as mock_reconstruct:
            mock_reconstruct.side_effect = [
                {"id": "orig", "model": "gpt-4", "choices": []},
                {"id": "final", "model": "gpt-4-turbo", "choices": []},
            ]

            await recorder.finalize_streaming()

            # Verify reconstruct called twice (once for ingress, once for egress)
            assert mock_reconstruct.call_count == 2
            mock_reconstruct.assert_any_call([chunk1])
            mock_reconstruct.assert_any_call([chunk2])

            # Verify event emitted
            observability.emit_event.assert_called_once()
            call_args = observability.emit_event.call_args
            assert call_args[1]["event_type"] == "transaction.streaming_response_recorded"
            assert call_args[1]["data"]["ingress_chunks"] == 1
            assert call_args[1]["data"]["egress_chunks"] == 1
            assert call_args[1]["data"]["original_response"]["id"] == "orig"
            assert call_args[1]["data"]["final_response"]["id"] == "final"

            # Verify metrics recorded
            assert observability.record_metric.call_count == 2
            observability.record_metric.assert_any_call("response.chunks.ingress", 1)
            observability.record_metric.assert_any_call("response.chunks.egress", 1)

    @pytest.mark.asyncio
    async def test_finalize_non_streaming_emits_responses(self):
        """finalize_non_streaming emits both responses."""
        observability = Mock(spec=ObservabilityContext)
        observability.emit_event = AsyncMock()
        recorder = DefaultTransactionRecorder(observability)

        # Create mock responses with finish_reason
        original = ModelResponse(
            id="orig",
            choices=[
                Choices(
                    index=0,
                    message=Message(content="original", role="assistant"),
                    finish_reason="stop",
                )
            ],
            model="gpt-4",
        )
        final = ModelResponse(
            id="final",
            choices=[
                Choices(
                    index=0,
                    message=Message(content="final", role="assistant"),
                    finish_reason="length",
                )
            ],
            model="gpt-4-turbo",
        )

        await recorder.finalize_non_streaming(original, final)

        # Verify event emitted
        observability.emit_event.assert_called_once()
        call_args = observability.emit_event.call_args
        assert call_args[1]["event_type"] == "transaction.non_streaming_response_recorded"
        assert call_args[1]["data"]["original_finish_reason"] == "stop"
        assert call_args[1]["data"]["final_finish_reason"] == "length"
        assert "original_response" in call_args[1]["data"]
        assert "final_response" in call_args[1]["data"]

        # Verify span attribute added
        observability.add_span_attribute.assert_called_once_with("response.finish_reason", "length")

    @pytest.mark.asyncio
    async def test_finalize_non_streaming_handles_missing_finish_reason(self):
        """finalize_non_streaming handles responses without finish_reason."""
        observability = Mock(spec=ObservabilityContext)
        observability.emit_event = AsyncMock()
        recorder = DefaultTransactionRecorder(observability)

        # Create mock responses without finish_reason
        original = ModelResponse(
            id="orig",
            choices=[],
            model="gpt-4",
        )
        final = ModelResponse(
            id="final",
            choices=[],
            model="gpt-4-turbo",
        )

        await recorder.finalize_non_streaming(original, final)

        # Verify event emitted with None finish_reason
        observability.emit_event.assert_called_once()
        call_args = observability.emit_event.call_args
        assert call_args[1]["data"]["original_finish_reason"] is None
        assert call_args[1]["data"]["final_finish_reason"] is None

        # Verify span attribute NOT added (because finish_reason is None)
        observability.add_span_attribute.assert_not_called()

    def test_get_finish_reason_extracts_correctly(self):
        """_get_finish_reason extracts finish_reason from response."""
        observability = Mock(spec=ObservabilityContext)
        recorder = DefaultTransactionRecorder(observability)

        response = ModelResponse(
            id="test",
            choices=[
                Choices(
                    index=0,
                    message=Message(content="test", role="assistant"),
                    finish_reason="stop",
                )
            ],
            model="gpt-4",
        )

        finish_reason = recorder._get_finish_reason(response)
        assert finish_reason == "stop"

    def test_get_finish_reason_returns_none_for_empty_choices(self):
        """_get_finish_reason returns None when no choices."""
        observability = Mock(spec=ObservabilityContext)
        recorder = DefaultTransactionRecorder(observability)

        response = ModelResponse(id="test", choices=[], model="gpt-4")

        finish_reason = recorder._get_finish_reason(response)
        assert finish_reason is None
