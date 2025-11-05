# ABOUTME: E2E tests for streaming Anthropic responses with PolicyOrchestrator
# ABOUTME: Tests with uppercase transformation policy to verify content handling

"""E2E tests for streaming Anthropic responses."""

import pytest
from opentelemetry import trace

from luthien_proxy.v2.llm.litellm_client import LiteLLMClient
from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.observability.context import NoOpObservabilityContext
from luthien_proxy.v2.observability.transaction_recorder import NoOpTransactionRecorder
from luthien_proxy.v2.orchestration.policy_orchestrator import PolicyOrchestrator
from luthien_proxy.v2.policies.simple_policy import SimplePolicy

tracer = trace.get_tracer(__name__)


class UppercasePolicy(SimplePolicy):
    """Test policy that uppercases all content."""

    async def on_response_content(self, content: str, request: Request) -> str:
        """Uppercase all content."""
        return content.upper()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_streaming_anthropic_with_uppercase_policy():
    """E2E test: Anthropic streaming with uppercase transformation."""
    policy = UppercasePolicy()
    llm_client = LiteLLMClient()

    request = Request(
        model="claude-haiku-4-5",
        messages=[{"role": "user", "content": "Say hello in 3 words"}],
        max_tokens=20,
    )

    # Process request
    with tracer.start_as_current_span("test_streaming_anthropic") as span:
        orchestrator = PolicyOrchestrator(
            policy=policy,
            llm_client=llm_client,
            observability=NoOpObservabilityContext(transaction_id="test-e2e", span=span),
            recorder=NoOpTransactionRecorder(),
        )
        final_request = await orchestrator.process_request(request, "test-txn-streaming-anthropic", span)

        # Verify request passed through unchanged
        assert final_request.model == "claude-haiku-4-5"

        # Process streaming response
        chunks = []
        async for chunk in orchestrator.process_streaming_response(final_request, "test-txn-streaming-anthropic", span):
            chunks.append(chunk)

    # Verify we got chunks
    assert len(chunks) > 0, "Should receive at least one chunk"

    # Reconstruct content from chunks
    content_parts = []
    for chunk in chunks:
        if chunk.choices and len(chunk.choices) > 0:
            delta = chunk.choices[0].delta
            if delta and hasattr(delta, "content") and delta.content:
                content_parts.append(delta.content)

    full_content = "".join(content_parts)

    # Verify content is uppercase
    assert full_content, "Should have non-empty content"
    assert full_content.isupper(), f"Content should be uppercase, got: {full_content}"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_streaming_anthropic_passthrough():
    """E2E test: Anthropic streaming with passthrough (no transformation)."""

    class PassthroughPolicy(SimplePolicy):
        """Policy that doesn't transform anything."""

        pass

    policy = PassthroughPolicy()
    llm_client = LiteLLMClient()

    request = Request(
        model="claude-haiku-4-5",
        messages=[{"role": "user", "content": "Say hello"}],
        max_tokens=20,
    )

    # Process request
    with tracer.start_as_current_span("test_streaming_anthropic_passthrough") as span:
        orchestrator = PolicyOrchestrator(
            policy=policy,
            llm_client=llm_client,
            observability=NoOpObservabilityContext(transaction_id="test-e2e", span=span),
            recorder=NoOpTransactionRecorder(),
        )
        final_request = await orchestrator.process_request(request, "test-txn-streaming-anthropic-passthrough", span)

        # Process streaming response
        chunks = []
        async for chunk in orchestrator.process_streaming_response(
            final_request, "test-txn-streaming-anthropic-passthrough", span
        ):
            chunks.append(chunk)

    # Verify we got chunks
    assert len(chunks) > 0, "Should receive at least one chunk"

    # Verify finish_reason is present in last chunk
    if chunks[-1].choices and len(chunks[-1].choices) > 0:
        finish_reason = chunks[-1].choices[0].finish_reason
        assert finish_reason in [
            "stop",
            "end_turn",
            "length",
            None,
        ], f"Unexpected finish_reason: {finish_reason}"
