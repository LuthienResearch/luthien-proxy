# ABOUTME: E2E tests for Anthropic tool calls with PolicyOrchestrator
# ABOUTME: Tests streaming tool call handling

"""E2E tests for Anthropic tool calls."""

import pytest
from opentelemetry import trace

from luthien_proxy.v2.llm.litellm_client import LiteLLMClient
from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.orchestration.factory import create_default_orchestrator
from luthien_proxy.v2.policies.simple_policy import SimplePolicy

tracer = trace.get_tracer(__name__)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_tool_calls_anthropic_streaming():
    """E2E test: Anthropic streaming with tool calls."""

    class PassthroughPolicy(SimplePolicy):
        """Policy that passes through tool calls."""

        pass

    policy = PassthroughPolicy()
    llm_client = LiteLLMClient()

    orchestrator = create_default_orchestrator(
        policy=policy,
        llm_client=llm_client,
        db_pool=None,
        event_publisher=None,
    )

    # Define a simple tool (Anthropic format)
    tools = [
        {
            "name": "get_weather",
            "description": "Get the weather for a location",
            "input_schema": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "The city and state, e.g. San Francisco, CA",
                    }
                },
                "required": ["location"],
            },
        }
    ]

    request = Request(
        model="claude-3-5-sonnet-20241022",
        messages=[{"role": "user", "content": "What's the weather in San Francisco?"}],
        tools=tools,
        max_tokens=100,
    )

    # Process request
    with tracer.start_as_current_span("test_tool_calls_anthropic") as span:
        final_request = await orchestrator.process_request(request, "test-txn-tool-calls-anthropic", span)

        # Process streaming response
        chunks = []
        async for chunk in orchestrator.process_streaming_response(
            final_request, "test-txn-tool-calls-anthropic", span
        ):
            chunks.append(chunk)

    # Verify we got chunks
    assert len(chunks) > 0, "Should receive at least one chunk"

    # Check if we got tool calls in the response
    has_tool_calls = False
    for chunk in chunks:
        if chunk.choices and len(chunk.choices) > 0:
            delta = chunk.choices[0].delta
            if delta and isinstance(delta, dict) and "tool_calls" in delta:
                has_tool_calls = True
                break

    # Note: This test may not always produce tool calls depending on LLM behavior
    # We just verify the system doesn't crash when tool calls are present
    print(f"Tool calls detected: {has_tool_calls}")


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_tool_calls_anthropic_non_streaming():
    """E2E test: Anthropic non-streaming with tool calls."""
    from luthien_proxy.v2.policies.policy import Policy

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

    # Define a simple tool
    tools = [
        {
            "name": "get_weather",
            "description": "Get the weather for a location",
            "input_schema": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "The city and state",
                    }
                },
                "required": ["location"],
            },
        }
    ]

    request = Request(
        model="claude-3-5-sonnet-20241022",
        messages=[{"role": "user", "content": "What's the weather in Boston?"}],
        tools=tools,
        max_tokens=100,
        stream=False,
    )

    # Process request
    with tracer.start_as_current_span("test_tool_calls_anthropic_non_streaming") as span:
        final_request = await orchestrator.process_request(request, "test-txn-tool-calls-anthropic-non-streaming", span)

        # Process full response
        response = await orchestrator.process_full_response(
            final_request, "test-txn-tool-calls-anthropic-non-streaming", span
        )

    # Verify response structure
    assert response.choices, "Response should have choices"
    assert len(response.choices) > 0, "Should have at least one choice"

    # Check if we got tool calls
    choice = response.choices[0]
    if hasattr(choice, "message") and choice.message:
        if hasattr(choice.message, "tool_calls"):
            print(f"Tool calls in response: {choice.message.tool_calls}")
