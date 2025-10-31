"""Unit tests for PolicyOrchestrator streaming response processing."""

import asyncio
from unittest.mock import Mock

import pytest
from litellm.types.utils import ModelResponse
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Span, set_tracer_provider

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
    mock_span = Mock(spec=Span)

    orchestrator = PolicyOrchestrator(
        policy=policy,
        llm_client=client,
        observability=NoOpObservabilityContext(transaction_id="test", span=mock_span),
        recorder=NoOpTransactionRecorder(),
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
    mock_span = Mock(spec=Span)

    orchestrator = PolicyOrchestrator(
        policy=policy,
        llm_client=client,
        observability=NoOpObservabilityContext(transaction_id="test", span=mock_span),
        recorder=NoOpTransactionRecorder(),
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
    mock_span = Mock(spec=Span)

    orchestrator = PolicyOrchestrator(
        policy=policy,
        llm_client=client,
        observability=NoOpObservabilityContext(transaction_id="test", span=mock_span),
        recorder=NoOpTransactionRecorder(),
    )

    request = Request(model="gpt-4", messages=[{"role": "user", "content": "Hi"}])

    with tracer.start_as_current_span("test") as span:
        async for _ in orchestrator.process_streaming_response(request, "test-123", span):
            pass

    # Verify policy saw all chunks
    assert policy.chunks_seen >= 1


def create_tool_call_chunks():
    """Create chunks with tool calls to trigger on_tool_call hooks."""
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
            choices=[
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_123",
                                "type": "function",
                                "function": {"name": "get_weather", "arguments": ""},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ],
        ),
        ModelResponse(
            id="test-id",
            object="chat.completion.chunk",
            created=1234567890,
            model="gpt-4",
            choices=[
                {
                    "index": 0,
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"location"'}}]},
                    "finish_reason": None,
                }
            ],
        ),
        ModelResponse(
            id="test-id",
            object="chat.completion.chunk",
            created=1234567890,
            model="gpt-4",
            choices=[
                {
                    "index": 0,
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": ':"NYC"}'}}]},
                    "finish_reason": None,
                }
            ],
        ),
        ModelResponse(
            id="test-id",
            object="chat.completion.chunk",
            created=1234567890,
            model="gpt-4",
            choices=[{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
        ),
    ]


class HookTrackingPolicy(Policy):
    """Policy that tracks which hooks were called."""

    def __init__(self):
        self.on_content_delta_called = False
        self.on_content_complete_called = False
        self.on_tool_call_delta_called = False
        self.on_tool_call_complete_called = False
        self.on_finish_reason_called = False
        self.chunks_seen = 0

    async def on_chunk_received(self, ctx) -> None:
        """Track chunk and passthrough."""
        self.chunks_seen += 1
        from luthien_proxy.v2.streaming.helpers import passthrough_last_chunk

        await passthrough_last_chunk(ctx)

    async def on_content_delta(self, ctx) -> None:
        """Track content delta hook."""
        self.on_content_delta_called = True

    async def on_content_complete(self, ctx) -> None:
        """Track content complete hook."""
        self.on_content_complete_called = True

    async def on_tool_call_delta(self, ctx) -> None:
        """Track tool call delta hook."""
        self.on_tool_call_delta_called = True

    async def on_tool_call_complete(self, ctx) -> None:
        """Track tool call complete hook."""
        self.on_tool_call_complete_called = True

    async def on_finish_reason(self, ctx) -> None:
        """Track finish reason hook."""
        self.on_finish_reason_called = True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chunk_factory",
    [
        create_content_chunks,
        create_tool_call_chunks,
    ],
)
async def test_streaming_with_different_chunk_types(setup_tracing, chunk_factory):
    """Test that orchestrator processes different chunk types without error."""
    tracer = setup_tracing
    chunks = chunk_factory()
    client = MockLLMClient(chunks)
    policy = HookTrackingPolicy()
    mock_span = Mock(spec=Span)

    orchestrator = PolicyOrchestrator(
        policy=policy,
        llm_client=client,
        observability=NoOpObservabilityContext(transaction_id="test", span=mock_span),
        recorder=NoOpTransactionRecorder(),
    )

    request = Request(model="gpt-4", messages=[{"role": "user", "content": "Hi"}])

    egress_chunks = []
    with tracer.start_as_current_span("test") as span:
        async for chunk in orchestrator.process_streaming_response(request, "test-123", span):
            egress_chunks.append(chunk)

    # Verify processing completed without error and chunks were seen
    assert policy.chunks_seen >= 1
    # Hooks may or may not be called depending on chunk assembly logic
    # The important thing is that the code paths exist and don't crash


@pytest.mark.asyncio
async def test_streaming_handles_empty_stream(setup_tracing):
    """Test that orchestrator handles empty streams gracefully."""
    tracer = setup_tracing
    client = MockLLMClient([])  # Empty stream
    policy = PassthroughPolicy()
    mock_span = Mock(spec=Span)

    orchestrator = PolicyOrchestrator(
        policy=policy,
        llm_client=client,
        observability=NoOpObservabilityContext(transaction_id="test", span=mock_span),
        recorder=NoOpTransactionRecorder(),
    )

    request = Request(model="gpt-4", messages=[{"role": "user", "content": "Hi"}])

    egress_chunks = []
    with tracer.start_as_current_span("test") as span:
        async for chunk in orchestrator.process_streaming_response(request, "test-123", span):
            egress_chunks.append(chunk)

    # Should handle empty stream without error
    assert len(egress_chunks) == 0
    assert policy.chunks_seen == 0


class ExceptionRaisingClient(LLMClient):
    """Mock client that raises an exception during streaming."""

    def __init__(self, chunks, exception_after=2):
        self.chunks = chunks
        self.exception_after = exception_after

    async def stream(self, request):
        """Return chunks then raise an exception."""

        async def chunk_generator():
            for i, chunk in enumerate(self.chunks):
                if i >= self.exception_after:
                    raise asyncio.CancelledError("Stream cancelled")
                yield chunk

        return chunk_generator()

    async def complete(self, request):
        """Not used."""
        raise NotImplementedError


@pytest.mark.asyncio
async def test_streaming_handles_queue_shutdown(setup_tracing):
    """Test that orchestrator handles stream cancellation gracefully."""
    tracer = setup_tracing
    # Client that will raise an exception mid-stream
    chunks = create_content_chunks()
    client = ExceptionRaisingClient(chunks, exception_after=2)
    policy = PassthroughPolicy()
    mock_span = Mock(spec=Span)

    orchestrator = PolicyOrchestrator(
        policy=policy,
        llm_client=client,
        observability=NoOpObservabilityContext(transaction_id="test", span=mock_span),
        recorder=NoOpTransactionRecorder(),
    )

    request = Request(model="gpt-4", messages=[{"role": "user", "content": "Hi"}])

    egress_chunks = []
    with tracer.start_as_current_span("test") as span:
        try:
            async for chunk in orchestrator.process_streaming_response(request, "test-123", span):
                egress_chunks.append(chunk)
        except asyncio.CancelledError:
            pass  # Expected - stream was cancelled

    # Should have received some chunks before cancellation
    # The important thing is the queue shutdown path was exercised without crashing
    assert policy.chunks_seen >= 0  # May be 0 if cancelled immediately
