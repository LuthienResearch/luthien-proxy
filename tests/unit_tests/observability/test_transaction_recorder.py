# ABOUTME: Unit tests for TransactionRecorder implementations
# ABOUTME: Tests NoOpTransactionRecorder and DefaultTransactionRecorder behavior

from unittest.mock import AsyncMock, Mock

import pytest
from litellm.types.utils import Choices, Delta, Message, ModelResponse, StreamingChoices

from luthien_proxy.messages import Request
from luthien_proxy.observability.context import ObservabilityContext
from luthien_proxy.observability.transaction_recorder import (
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
    async def test_record_response_does_nothing(self):
        """record_response does nothing and doesn't raise."""
        recorder = NoOpTransactionRecorder()
        original = Mock(spec=ModelResponse)
        final = Mock(spec=ModelResponse)
        await recorder.record_response(original, final)
        # No assertion - just verify it doesn't raise

    @pytest.mark.asyncio
    async def test_finalize_streaming_response_does_nothing(self):
        """finalize_streaming_response does nothing and doesn't raise."""
        recorder = NoOpTransactionRecorder()
        await recorder.finalize_streaming_response()
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

    @pytest.mark.asyncio
    async def test_ingress_chunks_within_limit_are_included(self):
        """Chunks within buffer limit are included in finalized output."""
        observability = Mock(spec=ObservabilityContext)
        observability.emit_event = AsyncMock()
        recorder = DefaultTransactionRecorder(observability, max_chunks_queued=3)

        # Add chunks within limit
        for i in range(3):
            chunk = ModelResponse(
                id=f"chunk-{i}",
                object="chat.completion.chunk",
                created=1234567890 + i,
                model="gpt-4",
                choices=[
                    StreamingChoices(
                        index=0,
                        delta=Delta(content=f"word{i}"),
                        finish_reason="stop" if i == 2 else None,
                    )
                ],
            )
            recorder.add_ingress_chunk(chunk)

        await recorder.finalize_streaming_response()

        # Verify all 3 chunks included in event
        call_args = observability.emit_event.call_args
        assert call_args[1]["data"]["ingress_chunks"] == 3
        # Verify content from all chunks present
        original_response = call_args[1]["data"]["original_response"]
        assert "word0word1word2" in original_response["choices"][0]["message"]["content"]

    @pytest.mark.asyncio
    async def test_ingress_chunks_beyond_limit_are_truncated(self):
        """Chunks beyond buffer limit are not included in finalized output."""
        observability = Mock(spec=ObservabilityContext)
        observability.emit_event = AsyncMock()
        observability.emit_event_nonblocking = Mock()
        recorder = DefaultTransactionRecorder(observability, max_chunks_queued=2)

        # Add chunks beyond limit
        for i in range(4):
            chunk = ModelResponse(
                id=f"chunk-{i}",
                object="chat.completion.chunk",
                created=1234567890 + i,
                model="gpt-4",
                choices=[
                    StreamingChoices(
                        index=0,
                        delta=Delta(content=f"word{i}"),
                        finish_reason="stop" if i == 3 else None,
                    )
                ],
            )
            recorder.add_ingress_chunk(chunk)

        await recorder.finalize_streaming_response()

        # Verify only first 2 chunks included
        call_args = observability.emit_event.call_args
        assert call_args[1]["data"]["ingress_chunks"] == 2
        # Verify only first 2 chunks' content present
        original_response = call_args[1]["data"]["original_response"]
        content = original_response["choices"][0]["message"]["content"]
        assert "word0word1" in content
        assert "word2" not in content
        assert "word3" not in content

    def test_ingress_truncation_emits_event(self):
        """Truncation event is emitted when ingress buffer limit exceeded."""
        observability = Mock(spec=ObservabilityContext)
        observability.emit_event_nonblocking = Mock()
        recorder = DefaultTransactionRecorder(observability, max_chunks_queued=2)

        # Add chunks up to and beyond limit
        for i in range(3):
            chunk = ModelResponse(
                id=f"chunk-{i}",
                object="chat.completion.chunk",
                created=1234567890 + i,
                model="gpt-4",
                choices=[StreamingChoices(index=0, delta=Delta(content=f"word{i}"), finish_reason=None)],
            )
            recorder.add_ingress_chunk(chunk)

        # Verify truncation event emitted once (for 3rd chunk)
        observability.emit_event_nonblocking.assert_called_once()
        call_args = observability.emit_event_nonblocking.call_args
        assert call_args[1]["event_type"] == "transaction.recorder.ingress_truncated"
        assert "max_chunks_queued_exceeded" in call_args[1]["data"]["reason"]

    @pytest.mark.asyncio
    async def test_egress_chunks_beyond_limit_are_truncated(self):
        """Chunks beyond buffer limit are not included in egress output."""
        observability = Mock(spec=ObservabilityContext)
        observability.emit_event = AsyncMock()
        observability.emit_event_nonblocking = Mock()
        recorder = DefaultTransactionRecorder(observability, max_chunks_queued=2)

        # Add egress chunks beyond limit
        for i in range(4):
            chunk = ModelResponse(
                id=f"chunk-{i}",
                object="chat.completion.chunk",
                created=1234567890 + i,
                model="gpt-4-turbo",
                choices=[
                    StreamingChoices(
                        index=0,
                        delta=Delta(content=f"response{i}"),
                        finish_reason="stop" if i == 3 else None,
                    )
                ],
            )
            recorder.add_egress_chunk(chunk)

        await recorder.finalize_streaming_response()

        # Verify only first 2 chunks included
        call_args = observability.emit_event.call_args
        assert call_args[1]["data"]["egress_chunks"] == 2
        # Verify only first 2 chunks' content present
        final_response = call_args[1]["data"]["final_response"]
        content = final_response["choices"][0]["message"]["content"]
        assert "response0response1" in content
        assert "response2" not in content
        assert "response3" not in content

    def test_egress_truncation_emits_event(self):
        """Truncation event is emitted when egress buffer limit exceeded."""
        observability = Mock(spec=ObservabilityContext)
        observability.emit_event_nonblocking = Mock()
        recorder = DefaultTransactionRecorder(observability, max_chunks_queued=2)

        # Add chunks up to and beyond limit
        for i in range(3):
            chunk = ModelResponse(
                id=f"chunk-{i}",
                object="chat.completion.chunk",
                created=1234567890 + i,
                model="gpt-4-turbo",
                choices=[StreamingChoices(index=0, delta=Delta(content=f"response{i}"), finish_reason=None)],
            )
            recorder.add_egress_chunk(chunk)

        # Verify truncation event emitted once (for 3rd chunk)
        observability.emit_event_nonblocking.assert_called_once()
        call_args = observability.emit_event_nonblocking.call_args
        assert call_args[1]["event_type"] == "transaction.recorder.egress_truncated"
        assert "max_chunks_queued_exceeded" in call_args[1]["data"]["reason"]

    @pytest.mark.asyncio
    async def test_finalize_streaming_response_reconstructs_and_emits(self):
        """finalize_streaming_response reconstructs responses and emits event."""
        observability = Mock(spec=ObservabilityContext)
        observability.emit_event = AsyncMock()
        recorder = DefaultTransactionRecorder(observability)

        # Add some realistic streaming chunks using proper types
        ingress_chunk = ModelResponse(
            id="ingress-id",
            object="chat.completion.chunk",
            created=1234567890,
            model="gpt-4",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(role="assistant", content="Hello"),
                    finish_reason=None,
                )
            ],
        )
        egress_chunk = ModelResponse(
            id="egress-id",
            object="chat.completion.chunk",
            created=1234567891,
            model="gpt-4-turbo",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(content="Hi"),
                    finish_reason="stop",
                )
            ],
        )
        recorder.add_ingress_chunk(ingress_chunk)
        recorder.add_egress_chunk(egress_chunk)

        await recorder.finalize_streaming_response()

        # Verify event emitted with reconstructed responses
        observability.emit_event.assert_called_once()
        call_args = observability.emit_event.call_args
        assert call_args[1]["event_type"] == "transaction.streaming_response_recorded"
        assert call_args[1]["data"]["ingress_chunks"] == 1
        assert call_args[1]["data"]["egress_chunks"] == 1

        # Verify reconstructed responses contain expected data
        original_response = call_args[1]["data"]["original_response"]
        final_response = call_args[1]["data"]["final_response"]
        assert original_response["id"] == "ingress-id"
        assert original_response["model"] == "gpt-4"
        assert "Hello" in original_response["choices"][0]["message"]["content"]
        assert final_response["id"] == "egress-id"
        assert final_response["model"] == "gpt-4-turbo"
        assert "Hi" in final_response["choices"][0]["message"]["content"]

        # Verify metrics recorded
        assert observability.record_metric.call_count == 2
        observability.record_metric.assert_any_call("response.chunks.ingress", 1)
        observability.record_metric.assert_any_call("response.chunks.egress", 1)

    @pytest.mark.asyncio
    async def test_record_response_emits_responses(self):
        """record_response emits both responses."""
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

        await recorder.record_response(original, final)

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
    async def test_record_response_handles_missing_finish_reason(self):
        """record_response handles responses without finish_reason."""
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

        await recorder.record_response(original, final)

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
