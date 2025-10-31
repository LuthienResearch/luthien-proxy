"""Unit tests for PolicyOrchestrator non-streaming response processing."""

from unittest.mock import AsyncMock, Mock

import pytest
from litellm.types.utils import ModelResponse
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Span, set_tracer_provider

from luthien_proxy.v2.llm.client import LLMClient
from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.observability.context import NoOpObservabilityContext
from luthien_proxy.v2.observability.transaction_recorder import NoOpTransactionRecorder
from luthien_proxy.v2.orchestration.policy_orchestrator import PolicyOrchestrator
from luthien_proxy.v2.policies.policy import Policy, PolicyContext


class PassthroughPolicy(Policy):
    """Policy that passes through responses unchanged."""

    pass  # Uses default implementations


class UppercasePolicy(Policy):
    """Policy that uppercases response content."""

    async def process_full_response(self, response: ModelResponse, context: PolicyContext) -> ModelResponse:
        """Uppercase the response content."""
        modified = response.model_copy(deep=True)
        if modified.choices and modified.choices[0].get("message"):
            message = modified.choices[0]["message"]
            if message.get("content"):
                message["content"] = message["content"].upper()
        return modified


class MockLLMClient(LLMClient):
    """Mock LLM client that returns predefined response."""

    def __init__(self, response):
        self.response = response

    async def complete(self, request):
        """Return predefined response."""
        return self.response

    async def stream(self, request):
        """Not used in non-streaming tests."""
        raise NotImplementedError


def create_sample_response(content: str = "Hello world") -> ModelResponse:
    """Create a sample non-streaming response."""
    return ModelResponse(
        id="test-id",
        object="chat.completion",
        created=1234567890,
        model="gpt-4",
        choices=[
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    )


@pytest.fixture
def setup_tracing():
    """Setup OpenTelemetry tracing for tests."""
    provider = TracerProvider()
    set_tracer_provider(provider)
    return provider.get_tracer(__name__)


@pytest.mark.asyncio
async def test_non_streaming_passthrough(setup_tracing):
    """Test non-streaming with passthrough policy."""
    tracer = setup_tracing
    response = create_sample_response("Hello world")
    client = MockLLMClient(response)
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
        final_response = await orchestrator.process_full_response(request, "test-123", span)  # noqa: F841

    # Should receive unchanged response
    assert final_response == response
    assert final_response.choices[0]["message"]["content"] == "Hello world"


@pytest.mark.asyncio
async def test_non_streaming_transformation(setup_tracing):
    """Test non-streaming with content transformation."""
    tracer = setup_tracing
    response = create_sample_response("Hello world")
    client = MockLLMClient(response)
    policy = UppercasePolicy()
    mock_span = Mock(spec=Span)

    orchestrator = PolicyOrchestrator(
        policy=policy,
        llm_client=client,
        observability=NoOpObservabilityContext(transaction_id="test", span=mock_span),
        recorder=NoOpTransactionRecorder(),
    )

    request = Request(model="gpt-4", messages=[{"role": "user", "content": "Hi"}])

    with tracer.start_as_current_span("test") as span:
        final_response = await orchestrator.process_full_response(request, "test-123", span)  # noqa: F841

    # Should receive transformed response
    assert final_response.choices[0]["message"]["content"] == "HELLO WORLD"
    # Original should be unchanged
    assert response.choices[0]["message"]["content"] == "Hello world"


@pytest.mark.asyncio
async def test_non_streaming_records_responses(setup_tracing):
    """Test that non-streaming records original and final responses."""
    tracer = setup_tracing
    response = create_sample_response("Hello world")
    client = MockLLMClient(response)
    policy = UppercasePolicy()

    # Mock recorder to track calls
    mock_recorder = AsyncMock()
    mock_recorder.finalize_non_streaming = AsyncMock()
    mock_span = Mock(spec=Span)

    orchestrator = PolicyOrchestrator(
        policy=policy,
        llm_client=client,
        observability=NoOpObservabilityContext(transaction_id="test", span=mock_span),
        recorder=NoOpTransactionRecorder(),
    )

    request = Request(model="gpt-4", messages=[{"role": "user", "content": "Hi"}])

    with tracer.start_as_current_span("test") as span:
        final_response = await orchestrator.process_full_response(request, "test-123", span)  # noqa: F841

    # Verify metadata preserved
    assert final_response.id == "test-id"
    assert final_response.model == "gpt-4"
    assert final_response.choices[0]["finish_reason"] == "stop"
    assert final_response.usage == response.usage


@pytest.mark.asyncio
async def test_non_streaming_calls_llm_client(setup_tracing):
    """Test that orchestrator calls llm_client.complete."""
    tracer = setup_tracing
    response = create_sample_response("Test")

    # Mock client to track calls
    mock_client = AsyncMock(spec=LLMClient)
    mock_client.complete = AsyncMock(return_value=response)

    policy = PassthroughPolicy()
    mock_span = Mock(spec=Span)

    orchestrator = PolicyOrchestrator(
        policy=policy,
        llm_client=mock_client,
        observability=NoOpObservabilityContext(transaction_id="test", span=mock_span),
        recorder=NoOpTransactionRecorder(),
    )

    request = Request(model="gpt-4", messages=[{"role": "user", "content": "Hi"}])

    with tracer.start_as_current_span("test") as span:
        final_response = await orchestrator.process_full_response(request, "test-123", span)  # noqa: F841

    # Verify client was called with request
    mock_client.complete.assert_called_once_with(request)
