"""Unit tests for PolicyOrchestrator request processing."""

from unittest.mock import Mock

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Span, set_tracer_provider

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
    span = Mock(spec=Span)

    return PolicyOrchestrator(
        policy=policy,
        llm_client=client,
        observability=NoOpObservabilityContext(transaction_id="test", span=span),
        recorder=NoOpTransactionRecorder(),
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
