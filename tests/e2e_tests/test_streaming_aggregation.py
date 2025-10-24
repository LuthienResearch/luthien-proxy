"""E2E tests for streaming aggregation patterns across different models.

These tests verify that our aggregation logic correctly handles real streaming
chunks from various LLM providers (OpenAI, Anthropic, etc.).
"""

import pytest
from litellm import acompletion

# Mark all tests in this file as e2e
pytestmark = pytest.mark.e2e


@pytest.fixture
def weather_tool():
    """Standard weather tool for testing tool calls."""
    return {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather for a location",
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string", "description": "City name"}},
                "required": ["location"],
            },
        },
    }


async def collect_chunks(model: str, messages: list, tools: list | None = None):
    """Collect all chunks from a streaming response.

    Returns:
        List of chunk dicts with parsed structure
    """
    response = await acompletion(
        model=model,
        messages=messages,
        tools=tools,
        stream=True,
        max_tokens=500,
        api_base="http://localhost:4000",
        api_key="sk-luthien-dev-key",
    )

    chunks = []
    async for chunk in response:
        chunk_dict = chunk.model_dump() if hasattr(chunk, "model_dump") else dict(chunk)
        chunks.append(chunk_dict)

    return chunks


def analyze_chunks(chunks: list) -> dict:
    """Analyze chunk structure for testing.

    Returns dict with:
        - content_chunks: Number of chunks with content
        - tool_call_chunks: Number of chunks with tool_calls
        - finish_chunk: Chunk with finish_reason (or None)
        - has_incremental_tool_calls: Whether tool calls arrived incrementally
        - role_chunk: First chunk with role
    """
    content_chunks = []
    tool_call_chunks = []
    finish_chunk = None
    role_chunk = None

    for chunk in chunks:
        choices = chunk.get("choices", [])
        if not choices:
            continue

        choice = choices[0]
        delta = choice.get("delta", {})
        finish_reason = choice.get("finish_reason")

        if delta.get("role") and not role_chunk:
            role_chunk = chunk

        if delta.get("content"):
            content_chunks.append(chunk)

        if delta.get("tool_calls"):
            tool_call_chunks.append(chunk)

        if finish_reason:
            finish_chunk = chunk

    # Check if tool calls are incremental (name and args in separate chunks)
    has_incremental_tool_calls = False
    if len(tool_call_chunks) > 1:
        if len(tool_call_chunks) > 1:
            # Check if later chunks have arguments (indicating streaming)
            for tc_chunk in tool_call_chunks[1:]:
                tc_delta = tc_chunk["choices"][0]["delta"]["tool_calls"][0]
                if tc_delta.get("function", {}).get("arguments"):
                    has_incremental_tool_calls = True
                    break

    return {
        "total_chunks": len(chunks),
        "content_chunks": len(content_chunks),
        "tool_call_chunks": len(tool_call_chunks),
        "finish_chunk": finish_chunk,
        "has_incremental_tool_calls": has_incremental_tool_calls,
        "role_chunk": role_chunk,
    }


@pytest.mark.asyncio
async def test_gpt5_text_streaming():
    """Test that gpt-5 streams text content incrementally."""
    chunks = await collect_chunks(
        model="gpt-5",
        messages=[{"role": "user", "content": "Count from 1 to 5"}],
    )

    analysis = analyze_chunks(chunks)

    # Should have multiple chunks
    assert analysis["total_chunks"] > 1, "Expected multiple chunks for streaming"

    # Should have content chunks
    assert analysis["content_chunks"] > 0, "Expected content chunks"

    # Should have role in first chunk
    assert analysis["role_chunk"] is not None, "Expected role in first chunk"

    # Should have finish_reason
    assert analysis["finish_chunk"] is not None, "Expected finish_reason chunk"
    assert analysis["finish_chunk"]["choices"][0]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_gpt5_tool_call_streaming(weather_tool):
    """Test that gpt-5 streams tool calls incrementally."""
    chunks = await collect_chunks(
        model="gpt-5",
        messages=[{"role": "user", "content": "What's the weather in Tokyo?"}],
        tools=[weather_tool],
    )

    analysis = analyze_chunks(chunks)

    # Should have tool call chunks
    assert analysis["tool_call_chunks"] > 0, "Expected tool call chunks"

    # Should have finish_reason=tool_calls
    assert analysis["finish_chunk"] is not None, "Expected finish_reason chunk"
    assert analysis["finish_chunk"]["choices"][0]["finish_reason"] == "tool_calls"

    # Document whether tool calls are incremental
    print(f"GPT-5 incremental tool calls: {analysis['has_incremental_tool_calls']}")


@pytest.mark.asyncio
async def test_gpt5_multiple_tool_calls(weather_tool):
    """Test gpt-5 with multiple parallel tool calls."""
    chunks = await collect_chunks(
        model="gpt-5",
        messages=[{"role": "user", "content": "Get weather for Tokyo and London"}],
        tools=[weather_tool],
    )

    analysis = analyze_chunks(chunks)

    # Should have tool call chunks
    assert analysis["tool_call_chunks"] > 0, "Expected tool call chunks"

    # Check if we got multiple tool calls
    # (We can't assert count without aggregation, just verify structure works)
    first_tc_chunk = None
    for chunk in chunks:
        choices = chunk.get("choices", [])
        if choices and choices[0].get("delta", {}).get("tool_calls"):
            first_tc_chunk = chunk
            break

    assert first_tc_chunk is not None, "Expected at least one tool call chunk"


@pytest.mark.asyncio
async def test_claude_sonnet_4_text_streaming():
    """Test that claude-sonnet-4 streams text content."""
    chunks = await collect_chunks(
        model="claude-sonnet-4",
        messages=[{"role": "user", "content": "Count from 1 to 5"}],
    )

    analysis = analyze_chunks(chunks)

    # Should have multiple chunks
    assert analysis["total_chunks"] > 1, "Expected multiple chunks for streaming"

    # Should have content chunks
    assert analysis["content_chunks"] > 0, "Expected content chunks"

    # Should have finish_reason
    assert analysis["finish_chunk"] is not None, "Expected finish_reason chunk"


@pytest.mark.asyncio
async def test_claude_sonnet_4_tool_call_streaming(weather_tool):
    """Test claude-sonnet-4 tool call streaming behavior."""
    chunks = await collect_chunks(
        model="claude-sonnet-4",
        messages=[{"role": "user", "content": "What's the weather in Paris?"}],
        tools=[weather_tool],
    )

    analysis = analyze_chunks(chunks)

    # Should have tool call chunks
    assert analysis["tool_call_chunks"] > 0, "Expected tool call chunks"

    # Document whether tool calls are incremental
    print(f"Claude Sonnet 4 incremental tool calls: {analysis['has_incremental_tool_calls']}")


@pytest.mark.asyncio
async def test_chunk_structure_consistency():
    """Verify all chunks have consistent structure across models."""
    models = ["gpt-5", "claude-sonnet-4"]

    for model in models:
        chunks = await collect_chunks(
            model=model,
            messages=[{"role": "user", "content": "Say hello"}],
        )

        # Every chunk should have required fields
        for i, chunk in enumerate(chunks):
            assert "id" in chunk, f"Chunk {i} from {model} missing 'id'"
            assert "choices" in chunk, f"Chunk {i} from {model} missing 'choices'"
            assert "model" in chunk, f"Chunk {i} from {model} missing 'model'"

            if chunk["choices"]:
                choice = chunk["choices"][0]
                assert "delta" in choice or "finish_reason" in choice, (
                    f"Chunk {i} from {model} has neither delta nor finish_reason"
                )


@pytest.mark.asyncio
async def test_finish_reason_patterns():
    """Document finish_reason patterns across models."""
    results = {}

    # Test text completion
    for model in ["gpt-5", "claude-sonnet-4"]:
        chunks = await collect_chunks(
            model=model,
            messages=[{"role": "user", "content": "Hello"}],
        )
        analysis = analyze_chunks(chunks)
        finish_reason = analysis["finish_chunk"]["choices"][0]["finish_reason"] if analysis["finish_chunk"] else None
        results[f"{model}_text"] = finish_reason

    # Test tool call completion
    weather_tool = {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get weather",
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"],
            },
        },
    }

    for model in ["gpt-5", "claude-sonnet-4"]:
        chunks = await collect_chunks(
            model=model,
            messages=[{"role": "user", "content": "What's the weather in NYC?"}],
            tools=[weather_tool],
        )
        analysis = analyze_chunks(chunks)
        finish_reason = analysis["finish_chunk"]["choices"][0]["finish_reason"] if analysis["finish_chunk"] else None
        results[f"{model}_tool_call"] = finish_reason

    # Document results
    print("\nFinish reason patterns:")
    for key, value in results.items():
        print(f"  {key}: {value}")

    # Verify we got finish reasons
    assert all(v is not None for v in results.values()), "All tests should have finish_reason"
