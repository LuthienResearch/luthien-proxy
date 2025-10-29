# ABOUTME: E2E tests for non-streaming Anthropic responses with PolicyOrchestrator
# ABOUTME: Tests full response transformation

"""E2E tests for non-streaming Anthropic responses."""

import pytest
from litellm.types.utils import ModelResponse
from opentelemetry import trace

from luthien_proxy.v2.llm.litellm_client import LiteLLMClient
from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.orchestration.factory import create_default_orchestrator
from luthien_proxy.v2.policies.policy import Policy, PolicyContext

tracer = trace.get_tracer(__name__)


class UppercaseNonStreamingPolicy(Policy):
    """Test policy that uppercases content in non-streaming responses."""

    async def process_full_response(self, response: ModelResponse, context: PolicyContext) -> ModelResponse:
        """Uppercase the response content."""
        # Extract content
        if response.choices and len(response.choices) > 0:
            choice = response.choices[0]
            if hasattr(choice, "message") and choice.message:
                content = choice.message.content
                if content:
                    # Uppercase it
                    choice.message.content = content.upper()

        return response


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_non_streaming_anthropic_with_uppercase_policy():
    """E2E test: Anthropic non-streaming with uppercase transformation."""
    policy = UppercaseNonStreamingPolicy()
    llm_client = LiteLLMClient()

    orchestrator = create_default_orchestrator(
        policy=policy,
        llm_client=llm_client,
        db_pool=None,
        event_publisher=None,
    )

    request = Request(
        model="claude-3-5-sonnet-20241022",
        messages=[{"role": "user", "content": "Say hello in 3 words"}],
        max_tokens=20,
        stream=False,
    )

    # Process request
    with tracer.start_as_current_span("test_non_streaming_anthropic") as span:
        final_request = await orchestrator.process_request(request, "test-txn-non-streaming-anthropic", span)

        # Verify request passed through unchanged
        assert final_request.model == "claude-3-5-sonnet-20241022"
        assert final_request.stream is False

        # Process full response
        response = await orchestrator.process_full_response(final_request, "test-txn-non-streaming-anthropic", span)

    # Verify response structure
    assert response.choices, "Response should have choices"
    assert len(response.choices) > 0, "Should have at least one choice"

    # Verify content is uppercase
    content = response.choices[0].message.content
    assert content, "Should have non-empty content"
    assert content.isupper(), f"Content should be uppercase, got: {content}"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_non_streaming_anthropic_passthrough():
    """E2E test: Anthropic non-streaming with passthrough (no transformation)."""

    class PassthroughPolicy(Policy):
        """Policy that doesn't transform anything."""

        pass

    policy = PassthroughPolicy()
    llm_client = LiteLLMClient()

    orchestrator = create_default_orchestrator(
        policy=policy,
        llm_client=llm_client,
        db_pool=None,
        event_publisher=None,
    )

    request = Request(
        model="claude-3-5-sonnet-20241022",
        messages=[{"role": "user", "content": "Say hello"}],
        max_tokens=20,
        stream=False,
    )

    # Process request
    with tracer.start_as_current_span("test_non_streaming_anthropic_passthrough") as span:
        final_request = await orchestrator.process_request(
            request, "test-txn-non-streaming-anthropic-passthrough", span
        )

        # Process full response
        response = await orchestrator.process_full_response(
            final_request, "test-txn-non-streaming-anthropic-passthrough", span
        )

    # Verify response structure
    assert response.choices, "Response should have choices"
    assert len(response.choices) > 0, "Should have at least one choice"
    assert response.choices[0].message.content, "Should have content"

    # Verify finish_reason
    finish_reason = response.choices[0].finish_reason
    assert finish_reason in [
        "stop",
        "end_turn",
        "length",
    ], f"Unexpected finish_reason: {finish_reason}"
