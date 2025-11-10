"""Unit tests for PolicyOrchestrator request processing and transaction recording."""

import pytest
from litellm.types.utils import Choices, Message, ModelResponse
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import set_tracer_provider

from luthien_proxy.v2.llm.client import LLMClient
from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.observability.context import NoOpObservabilityContext
from luthien_proxy.v2.observability.transaction_recorder import NoOpTransactionRecorder
from luthien_proxy.v2.orchestration.policy_orchestrator import PolicyOrchestrator
from luthien_proxy.v2.policies import PolicyContext
from luthien_proxy.v2.policy_core.policy_protocol import PolicyProtocol


class MockPolicy(PolicyProtocol):
    """Mock policy for testing."""

    def __init__(self):
        self.on_request_called = False
        self.request_seen = None

    async def on_request(self, request: Request, context: PolicyContext) -> Request:
        """Track that on_request was called."""
        self.on_request_called = True
        self.request_seen = request
        # Modify request to verify transformation
        modified = request.model_copy(deep=True)
        modified.temperature = 0.5
        return modified


class MockLLMClient(LLMClient):
    """Mock LLM client for testing."""

    async def stream(self, request):
        """Not used in request processing tests."""
        raise NotImplementedError

    async def complete(self, request):
        """Not used in request processing tests."""
        raise NotImplementedError


@pytest.fixture
def setup_tracing():
    """Setup OpenTelemetry tracing for tests."""
    provider = TracerProvider()
    set_tracer_provider(provider)
    return provider.get_tracer(__name__)


@pytest.fixture
def orchestrator(setup_tracing):
    """Create orchestrator with mock policy."""
    from luthien_proxy.v2.streaming.client_formatter.openai import OpenAIClientFormatter
    from luthien_proxy.v2.streaming.policy_executor import PolicyExecutor

    policy = MockPolicy()
    recorder = NoOpTransactionRecorder()
    policy_executor = PolicyExecutor(recorder=recorder)
    client_formatter = OpenAIClientFormatter(model_name="gpt-4")

    return PolicyOrchestrator(
        policy=policy,
        policy_executor=policy_executor,
        client_formatter=client_formatter,
        transaction_recorder=recorder,
    ), policy


@pytest.mark.asyncio
async def test_process_request_calls_policy(orchestrator, setup_tracing):
    """Test that process_request calls policy.on_request."""
    from luthien_proxy.v2.policies import PolicyContext

    orch, policy = orchestrator
    tracer = setup_tracing

    request = Request(
        model="gpt-4",
        messages=[{"role": "user", "content": "Hello"}],
        temperature=1.0,
    )

    with tracer.start_as_current_span("test") as span:
        obs_ctx = NoOpObservabilityContext(transaction_id="test-123", span=span)
        policy_ctx = PolicyContext(transaction_id="test-123", request=request, observability=obs_ctx)
        final_request = await orch.process_request(request, policy_ctx, obs_ctx)

    # Verify policy was called
    assert policy.on_request_called
    assert policy.request_seen == request

    # Verify transformation applied
    assert final_request.temperature == 0.5
    assert request.temperature == 1.0  # Original unchanged


@pytest.mark.asyncio
async def test_process_request_preserves_request_fields(orchestrator, setup_tracing):
    """Test that non-modified request fields are preserved."""
    from luthien_proxy.v2.policies import PolicyContext

    orch, policy = orchestrator
    tracer = setup_tracing

    request = Request(
        model="gpt-4",
        messages=[{"role": "user", "content": "Hello"}],
        temperature=1.0,
        max_tokens=100,
        top_p=0.9,
    )

    with tracer.start_as_current_span("test") as span:
        obs_ctx = NoOpObservabilityContext(transaction_id="test-123", span=span)
        policy_ctx = PolicyContext(transaction_id="test-123", request=request, observability=obs_ctx)
        final_request = await orch.process_request(request, policy_ctx, obs_ctx)

    # Verify unmodified fields preserved
    assert final_request.model == "gpt-4"
    assert final_request.messages == request.messages
    assert final_request.max_tokens == 100
    assert final_request.top_p == 0.9
    # Only temperature was modified by MockPolicy
    assert final_request.temperature == 0.5


# ============================================================================
# Transaction Recording Tests
# ============================================================================


@pytest.fixture
def orchestrator_with_recording(setup_tracing):
    """Create orchestrator with real recorder and spy on recording methods."""
    from unittest.mock import AsyncMock

    from luthien_proxy.v2.policies.noop_policy import NoOpPolicy
    from luthien_proxy.v2.streaming.client_formatter.openai import OpenAIClientFormatter
    from luthien_proxy.v2.streaming.policy_executor import PolicyExecutor

    policy = NoOpPolicy()
    recorder = NoOpTransactionRecorder()

    # Spy on recorder methods
    recorder.record_request = AsyncMock(wraps=recorder.record_request)
    recorder.record_response = AsyncMock(wraps=recorder.record_response)

    policy_executor = PolicyExecutor(recorder=recorder)
    client_formatter = OpenAIClientFormatter(model_name="gpt-4")

    return PolicyOrchestrator(
        policy=policy,
        policy_executor=policy_executor,
        client_formatter=client_formatter,
        transaction_recorder=recorder,
    ), recorder


@pytest.mark.asyncio
async def test_process_request_records_transaction(orchestrator_with_recording, setup_tracing):
    """Test that process_request calls record_request with original and final requests."""
    orch, recorder = orchestrator_with_recording
    tracer = setup_tracing

    original_request = Request(
        model="gpt-4",
        messages=[{"role": "user", "content": "Hello"}],
        temperature=1.0,
    )

    with tracer.start_as_current_span("test") as span:
        obs_ctx = NoOpObservabilityContext(transaction_id="test-123", span=span)
        policy_ctx = PolicyContext(transaction_id="test-123", request=original_request, observability=obs_ctx)
        final_request = await orch.process_request(original_request, policy_ctx, obs_ctx)

    # Verify recorder.record_request was called
    recorder.record_request.assert_called_once()

    # Verify correct arguments passed
    call_args = recorder.record_request.call_args
    assert call_args[0][0] == original_request  # First positional arg
    assert call_args[0][1] == final_request  # Second positional arg


@pytest.mark.asyncio
async def test_process_full_response_records_transaction(orchestrator_with_recording, setup_tracing):
    """Test that process_full_response calls record_response with original and final responses."""
    orch, recorder = orchestrator_with_recording
    tracer = setup_tracing

    original_response = ModelResponse(
        id="test-id",
        model="gpt-4",
        choices=[
            Choices(
                index=0,
                message=Message(content="Hello world", role="assistant"),
                finish_reason="stop",
            )
        ],
    )

    with tracer.start_as_current_span("test") as span:
        obs_ctx = NoOpObservabilityContext(transaction_id="test-123", span=span)
        policy_ctx = PolicyContext(transaction_id="test-123", request=None, observability=obs_ctx)
        final_response = await orch.process_full_response(original_response, policy_ctx)

    # Verify recorder.record_response was called
    recorder.record_response.assert_called_once()

    # Verify correct arguments passed
    call_args = recorder.record_response.call_args
    assert call_args[0][0] == original_response  # First positional arg
    assert call_args[0][1] == final_response  # Second positional arg
