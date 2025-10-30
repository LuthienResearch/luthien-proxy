"""Unit tests for PolicyOrchestrator streaming response processing."""

from unittest.mock import AsyncMock

import pytest
from litellm.types.utils import ModelResponse
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import set_tracer_provider

from luthien_proxy.v2.llm.client import LLMClient
from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.observability.context import NoOpObservabilityContext
from luthien_proxy.v2.observability.transaction_recorder import NoOpTransactionRecorder
from luthien_proxy.v2.orchestration.policy_orchestrator import PolicyOrchestrator
from luthien_proxy.v2.policies.policy import Policy


class PassthroughPolicy(Policy):
    """Policy that passes through chunks unchanged by using on_chunk_received hook."""

    def __init__(self):
        self.chunks_seen = 0

    async def on_chunk_received(self, ctx) -> None:
        """Count chunks and passthrough."""
        self.chunks_seen += 1
        from luthien_proxy.v2.streaming.helpers import passthrough_last_chunk

        await passthrough_last_chunk(ctx)


class MockLLMClient(LLMClient):
    """Mock LLM client that returns predefined chunks."""

    def __init__(self, chunks):
        self.chunks = chunks
        self.stream_called = False

    async def stream(self, request):
        """Return async iterator of predefined chunks."""
        self.stream_called = True

        async def chunk_generator():
            for chunk in self.chunks:
                yield chunk

        return chunk_generator()

    async def complete(self, request):
        """Not used in streaming tests."""
        raise NotImplementedError


def create_content_chunks():
    """Create sample content chunks."""
    return [
        ModelResponse(
            id="test-id",
            object="chat.completion.chunk",
            created=1234567890,
            model="gpt-4",
            choices=[{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        ),
        ModelResponse(
            id="test-id",
            object="chat.completion.chunk",
            created=1234567890,
            model="gpt-4",
            choices=[{"index": 0, "delta": {"content": "Hello"}, "finish_reason": None}],
        ),
        ModelResponse(
            id="test-id",
            object="chat.completion.chunk",
            created=1234567890,
            model="gpt-4",
            choices=[{"index": 0, "delta": {"content": " world"}, "finish_reason": None}],
        ),
        ModelResponse(
            id="test-id",
            object="chat.completion.chunk",
            created=1234567890,
            model="gpt-4",
            choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}],
        ),
    ]


@pytest.fixture
def setup_tracing():
    """Setup OpenTelemetry tracing for tests."""
    provider = TracerProvider()
    set_tracer_provider(provider)
    return provider.get_tracer(__name__)


@pytest.mark.asyncio
async def test_streaming_calls_llm_client(setup_tracing):
    """Test that orchestrator calls llm_client.stream."""
    tracer = setup_tracing
    chunks = create_content_chunks()
    client = MockLLMClient(chunks)
    policy = PassthroughPolicy()

    def observability_factory(transaction_id, span):
        return NoOpObservabilityContext(transaction_id)

    def recorder_factory(observability):
        return NoOpTransactionRecorder()

    orchestrator = PolicyOrchestrator(
        policy=policy,
        llm_client=client,
        observability_factory=observability_factory,
        recorder_factory=recorder_factory,
    )

    request = Request(model="gpt-4", messages=[{"role": "user", "content": "Hi"}])

    with tracer.start_as_current_span("test") as span:
        async for _ in orchestrator.process_streaming_response(request, "test-123", span):
            pass

    # Verify client.stream was called
    assert client.stream_called


@pytest.mark.asyncio
async def test_streaming_yields_chunks(setup_tracing):
    """Test that orchestrator yields chunks from policy."""
    tracer = setup_tracing
    chunks = create_content_chunks()
    client = MockLLMClient(chunks)
    policy = PassthroughPolicy()

    def observability_factory(transaction_id, span):
        return NoOpObservabilityContext(transaction_id)

    def recorder_factory(observability):
        return NoOpTransactionRecorder()

    orchestrator = PolicyOrchestrator(
        policy=policy,
        llm_client=client,
        observability_factory=observability_factory,
        recorder_factory=recorder_factory,
    )

    request = Request(model="gpt-4", messages=[{"role": "user", "content": "Hi"}])

    egress_chunks = []
    with tracer.start_as_current_span("test") as span:
        async for chunk in orchestrator.process_streaming_response(request, "test-123", span):
            egress_chunks.append(chunk)

    # Should receive chunks (passthrough policy emits them)
    assert len(egress_chunks) >= 1


@pytest.mark.asyncio
async def test_streaming_calls_policy_hooks(setup_tracing):
    """Test that policy hooks are called during streaming."""
    tracer = setup_tracing
    chunks = create_content_chunks()
    client = MockLLMClient(chunks)
    policy = PassthroughPolicy()

    def observability_factory(transaction_id, span):
        return NoOpObservabilityContext(transaction_id)

    def recorder_factory(observability):
        return NoOpTransactionRecorder()

    orchestrator = PolicyOrchestrator(
        policy=policy,
        llm_client=client,
        observability_factory=observability_factory,
        recorder_factory=recorder_factory,
    )

    request = Request(model="gpt-4", messages=[{"role": "user", "content": "Hi"}])

    with tracer.start_as_current_span("test") as span:
        async for _ in orchestrator.process_streaming_response(request, "test-123", span):
            pass

    # Verify policy saw all chunks
    assert policy.chunks_seen >= 1


@pytest.mark.asyncio
async def test_streaming_records_chunks(setup_tracing):
    """Test that streaming records ingress and egress chunks."""
    tracer = setup_tracing
    chunks = create_content_chunks()
    client = MockLLMClient(chunks)
    policy = PassthroughPolicy()

    def observability_factory(transaction_id, span):
        return NoOpObservabilityContext(transaction_id)

    # Mock recorder to track calls
    mock_recorder = AsyncMock()
    mock_recorder.add_ingress_chunk = AsyncMock()
    mock_recorder.add_egress_chunk = AsyncMock()
    mock_recorder.finalize_streaming = AsyncMock()

    def recorder_factory(observability):
        return mock_recorder

    orchestrator = PolicyOrchestrator(
        policy=policy,
        llm_client=client,
        observability_factory=observability_factory,
        recorder_factory=recorder_factory,
    )

    request = Request(model="gpt-4", messages=[{"role": "user", "content": "Hi"}])

    with tracer.start_as_current_span("test") as span:
        async for _ in orchestrator.process_streaming_response(request, "test-123", span):
            pass

    # Verify chunks were recorded
    assert mock_recorder.add_ingress_chunk.call_count >= 1
    assert mock_recorder.add_egress_chunk.call_count >= 1
    mock_recorder.finalize_streaming.assert_called_once()


@pytest.mark.asyncio
async def test_streaming_empty_response(setup_tracing):
    """Test streaming with empty response."""
    tracer = setup_tracing
    client = MockLLMClient([])  # No chunks
    policy = PassthroughPolicy()

    def observability_factory(transaction_id, span):
        return NoOpObservabilityContext(transaction_id)

    def recorder_factory(observability):
        return NoOpTransactionRecorder()

    orchestrator = PolicyOrchestrator(
        policy=policy,
        llm_client=client,
        observability_factory=observability_factory,
        recorder_factory=recorder_factory,
    )

    request = Request(model="gpt-4", messages=[{"role": "user", "content": "Hi"}])

    egress_chunks = []
    with tracer.start_as_current_span("test") as span:
        async for chunk in orchestrator.process_streaming_response(request, "test-123", span):
            egress_chunks.append(chunk)

    # Should handle empty stream gracefully
    assert len(egress_chunks) == 0
    assert policy.chunks_seen == 0
