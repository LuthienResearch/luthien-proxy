"""Unit tests for PolicyOrchestrator request processing."""

from unittest.mock import AsyncMock

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import set_tracer_provider

from luthien_proxy.v2.llm.client import LLMClient
from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.observability.context import NoOpObservabilityContext
from luthien_proxy.v2.observability.transaction_recorder import NoOpTransactionRecorder
from luthien_proxy.v2.orchestration.policy_orchestrator import PolicyOrchestrator
from luthien_proxy.v2.policies.policy import Policy, PolicyContext


class MockPolicy(Policy):
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
    policy = MockPolicy()
    client = MockLLMClient()

    def observability_factory(transaction_id, span):
        return NoOpObservabilityContext(transaction_id)

    def recorder_factory(observability):
        return NoOpTransactionRecorder()

    return PolicyOrchestrator(
        policy=policy,
        llm_client=client,
        observability_factory=observability_factory,
        recorder_factory=recorder_factory,
    ), policy


@pytest.mark.asyncio
async def test_process_request_calls_policy(orchestrator, setup_tracing):
    """Test that process_request calls policy.on_request."""
    orch, policy = orchestrator
    tracer = setup_tracing

    request = Request(
        model="gpt-4",
        messages=[{"role": "user", "content": "Hello"}],
        temperature=1.0,
    )

    with tracer.start_as_current_span("test") as span:
        final_request = await orch.process_request(request, "test-123", span)

    # Verify policy was called
    assert policy.on_request_called
    assert policy.request_seen == request

    # Verify transformation applied
    assert final_request.temperature == 0.5
    assert request.temperature == 1.0  # Original unchanged


@pytest.mark.asyncio
async def test_process_request_records_original_and_final(orchestrator, setup_tracing):
    """Test that both original and final requests are recorded."""
    orch, policy = orchestrator
    tracer = setup_tracing

    # Mock the recorder to track calls
    mock_recorder = AsyncMock()
    orch.recorder_factory = lambda obs: mock_recorder

    request = Request(
        model="gpt-4",
        messages=[{"role": "user", "content": "Hello"}],
        temperature=1.0,
    )

    with tracer.start_as_current_span("test") as span:
        final_request = await orch.process_request(request, "test-123", span)

    # Verify recorder.record_request was called with original and final
    mock_recorder.record_request.assert_called_once()
    call_args = mock_recorder.record_request.call_args[0]
    assert call_args[0] == request  # Original
    assert call_args[1] == final_request  # Final
    assert call_args[0].temperature == 1.0
    assert call_args[1].temperature == 0.5


@pytest.mark.asyncio
async def test_process_request_creates_observability_context(orchestrator, setup_tracing):
    """Test that observability context is created with correct transaction_id."""
    orch, policy = orchestrator
    tracer = setup_tracing

    # Track observability creation
    created_contexts = []

    def tracking_factory(transaction_id, span):
        ctx = NoOpObservabilityContext(transaction_id)
        created_contexts.append((transaction_id, ctx))
        return ctx

    orch.observability_factory = tracking_factory

    request = Request(model="gpt-4", messages=[{"role": "user", "content": "Hello"}])

    with tracer.start_as_current_span("test") as span:
        await orch.process_request(request, "test-tx-456", span)

    # Verify context created with correct ID
    assert len(created_contexts) == 1
    assert created_contexts[0][0] == "test-tx-456"


@pytest.mark.asyncio
async def test_process_request_preserves_request_fields(orchestrator, setup_tracing):
    """Test that non-modified request fields are preserved."""
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
        final_request = await orch.process_request(request, "test-123", span)

    # Verify unmodified fields preserved
    assert final_request.model == "gpt-4"
    assert final_request.messages == request.messages
    assert final_request.max_tokens == 100
    assert final_request.top_p == 0.9
    # Only temperature was modified by MockPolicy
    assert final_request.temperature == 0.5
