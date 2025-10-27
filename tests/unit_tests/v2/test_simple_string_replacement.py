"""ABOUTME: Unit tests for SimpleStringReplacementPolicy.
ABOUTME: Tests string replacement logic with various configurations."""

import asyncio
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from litellm.types.utils import ModelResponse

from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.policies.policy_context import PolicyContext
from luthien_proxy.v2.policies.simple_string_replacement import (
    SimpleStringReplacementPolicy,
)

FIXTURE_DIR = Path(__file__).parent / "streaming" / "chunk_fixtures"


def load_chunks(filename: str) -> list[dict]:
    """Load chunk data from JSON file."""
    path = FIXTURE_DIR / filename
    with path.open() as f:
        return json.load(f)


async def run_policy_on_stream(
    policy: SimpleStringReplacementPolicy,
    chunks: list[dict],
    request: Request | None = None,
) -> list[ModelResponse]:
    """Run policy on stream and collect output chunks."""
    if request is None:
        request = Request(
            model="claude-3-opus-20240229",
            messages=[{"role": "user", "content": "test"}],
        )

    # Create queues
    incoming = asyncio.Queue()
    outgoing = asyncio.Queue()

    # Create mock span
    mock_span = Mock()
    mock_span.add_event = Mock()

    # Create context
    context = PolicyContext(
        call_id="test-call-id",
        span=mock_span,
        request=request,
    )

    # Populate incoming queue
    for chunk_dict in chunks:
        mr = ModelResponse(**chunk_dict)
        # Fix finish_reason
        if chunk_dict.get("choices"):
            original_finish = chunk_dict["choices"][0].get("finish_reason")
            if mr.choices and mr.choices[0].finish_reason != original_finish:
                mr.choices[0].finish_reason = original_finish
        await incoming.put(mr)

    incoming.shutdown()

    # Process stream
    await policy.process_streaming_response(
        incoming=incoming,
        outgoing=outgoing,
        context=context,
    )

    # Collect output
    output = []
    while True:
        try:
            chunk = outgoing.get_nowait()
            output.append(chunk)
        except (asyncio.QueueEmpty, asyncio.QueueShutDown):
            break

    return output


def extract_content_from_chunks(chunks: list[ModelResponse]) -> str:
    """Extract complete content text from output chunks."""
    content_parts = []
    for chunk in chunks:
        if chunk.choices and chunk.choices[0].delta.content:
            content_parts.append(chunk.choices[0].delta.content)
    return "".join(content_parts)


@pytest.mark.asyncio
async def test_empty_replacements():
    """Test that policy with no replacements passes content through unchanged."""
    chunks = load_chunks("no_tools_used_chunks.json")
    policy = SimpleStringReplacementPolicy(replacements={})
    output = await run_policy_on_stream(policy, chunks)

    # Content should be unchanged
    content = extract_content_from_chunks(output)
    assert content.startswith("Here are the major cities")


@pytest.mark.asyncio
async def test_single_replacement():
    """Test single string replacement."""
    chunks = load_chunks("no_tools_used_chunks.json")
    policy = SimpleStringReplacementPolicy(replacements={"Tokyo": "TOKYO"})
    output = await run_policy_on_stream(policy, chunks)

    content = extract_content_from_chunks(output)
    # Should have replaced Tokyo with TOKYO
    assert "TOKYO" in content
    assert "Tokyo" not in content


@pytest.mark.asyncio
async def test_multiple_replacements():
    """Test multiple string replacements."""
    chunks = load_chunks("no_tools_used_chunks.json")
    policy = SimpleStringReplacementPolicy(
        replacements={
            "Tokyo": "TOKYO",
            "Japan": "JAPAN",
            "cities": "CITIES",
        }
    )
    output = await run_policy_on_stream(policy, chunks)

    content = extract_content_from_chunks(output)
    # All replacements should be applied
    assert "TOKYO" in content
    assert "JAPAN" in content
    assert "CITIES" in content
    # Original strings should not remain
    assert "Tokyo" not in content
    assert "Japan" not in content
    assert "cities" not in content


@pytest.mark.asyncio
async def test_case_sensitive_replacement():
    """Test that replacements are case-sensitive."""
    chunks = load_chunks("no_tools_used_chunks.json")
    policy = SimpleStringReplacementPolicy(replacements={"tokyo": "REPLACED"})
    output = await run_policy_on_stream(policy, chunks)

    content = extract_content_from_chunks(output)
    # Should NOT replace "Tokyo" (different case)
    assert "Tokyo" in content
    assert "REPLACED" not in content


@pytest.mark.asyncio
async def test_replacement_order_matters():
    """Test that replacements are applied in order and can affect each other."""
    chunks = load_chunks("no_tools_used_chunks.json")

    # First replace "major" with "MAJOR", then "MAJOR" with "HUGE"
    policy = SimpleStringReplacementPolicy(
        replacements={
            "major": "MAJOR",
            "MAJOR": "HUGE",
        }
    )
    output = await run_policy_on_stream(policy, chunks)

    content = extract_content_from_chunks(output)
    # Should end up with "HUGE" (both replacements applied)
    assert "HUGE" in content
    assert "major" not in content
    assert "MAJOR" not in content


@pytest.mark.asyncio
async def test_no_match_no_change():
    """Test that content is unchanged when replacements don't match."""
    chunks = load_chunks("no_tools_used_chunks.json")
    policy = SimpleStringReplacementPolicy(
        replacements={
            "nonexistent": "REPLACEMENT",
            "not_in_content": "ANOTHER",
        }
    )
    output = await run_policy_on_stream(policy, chunks)

    content = extract_content_from_chunks(output)
    # Content should be unchanged
    assert content.startswith("Here are the major cities")
    assert "REPLACEMENT" not in content
    assert "ANOTHER" not in content


@pytest.mark.asyncio
async def test_replacement_with_empty_string():
    """Test replacing with empty string (deletion)."""
    chunks = load_chunks("no_tools_used_chunks.json")
    policy = SimpleStringReplacementPolicy(replacements={"Tokyo": ""})
    output = await run_policy_on_stream(policy, chunks)

    content = extract_content_from_chunks(output)
    # "Tokyo" should be removed
    assert "Tokyo" not in content


@pytest.mark.asyncio
async def test_replacement_with_longer_string():
    """Test replacing with a longer string."""
    chunks = load_chunks("no_tools_used_chunks.json")
    policy = SimpleStringReplacementPolicy(replacements={"Tokyo": "Tokyo (the capital of Japan)"})
    output = await run_policy_on_stream(policy, chunks)

    content = extract_content_from_chunks(output)
    # Should have the expanded text
    assert "Tokyo (the capital of Japan)" in content


@pytest.mark.asyncio
async def test_multiple_occurrences():
    """Test that all occurrences of a string are replaced."""
    chunks = load_chunks("no_tools_used_chunks.json")

    # The content mentions "Japan" multiple times
    policy = SimpleStringReplacementPolicy(replacements={"Japan": "JAPAN"})
    output = await run_policy_on_stream(policy, chunks)

    content = extract_content_from_chunks(output)
    # All occurrences should be replaced
    assert content.count("JAPAN") > 0
    assert "Japan" not in content


@pytest.mark.asyncio
async def test_tool_calls_pass_through():
    """Test that tool calls are not affected by replacements."""
    chunks = load_chunks("anthropic_multiple_tools_chunks.json")

    # Try to replace strings that might appear in tool calls
    policy = SimpleStringReplacementPolicy(
        replacements={
            "get_weather": "REPLACED_TOOL",
            "location": "REPLACED_PARAM",
        }
    )
    output = await run_policy_on_stream(policy, chunks)

    # Extract tool calls
    tool_chunks = [
        c for c in output if c.choices and hasattr(c.choices[0].delta, "tool_calls") and c.choices[0].delta.tool_calls
    ]

    # Tool calls should be unchanged (replacements only apply to content)
    for chunk in tool_chunks:
        tool_call = chunk.choices[0].delta.tool_calls[0]
        # Original tool names should remain
        assert tool_call["function"]["name"] in ["get_weather", "get_time"]
        # Parse arguments to check they're unchanged
        args = json.loads(tool_call["function"]["arguments"])
        assert "location" in args


@pytest.mark.asyncio
async def test_event_emission():
    """Test that policy emits events when replacements are made."""
    chunks = load_chunks("no_tools_used_chunks.json")
    policy = SimpleStringReplacementPolicy(replacements={"Tokyo": "TOKYO"})

    # Create mock span to track events
    mock_span = Mock()
    mock_span.add_event = Mock()

    request = Request(
        model="claude-3-opus-20240229",
        messages=[{"role": "user", "content": "test"}],
    )

    context = PolicyContext(
        call_id="test-call-id",
        span=mock_span,
        request=request,
    )

    incoming = asyncio.Queue()
    outgoing = asyncio.Queue()

    for chunk_dict in chunks:
        mr = ModelResponse(**chunk_dict)
        if chunk_dict.get("choices"):
            original_finish = chunk_dict["choices"][0].get("finish_reason")
            if mr.choices and mr.choices[0].finish_reason != original_finish:
                mr.choices[0].finish_reason = original_finish
        await incoming.put(mr)

    incoming.shutdown()

    await policy.process_streaming_response(
        incoming=incoming,
        outgoing=outgoing,
        context=context,
    )

    # Check that an event was emitted
    mock_span.add_event.assert_called()
    # Find the content_transformed event
    calls = [call for call in mock_span.add_event.call_args_list]
    event_types = [call[0][0] for call in calls]
    assert "policy.content_transformed" in event_types


@pytest.mark.asyncio
async def test_default_empty_config():
    """Test that policy works with default empty config."""
    policy = SimpleStringReplacementPolicy()
    assert policy.replacements == {}

    chunks = load_chunks("no_tools_used_chunks.json")
    output = await run_policy_on_stream(policy, chunks)

    # Should pass through unchanged
    content = extract_content_from_chunks(output)
    assert content.startswith("Here are the major cities")


@pytest.mark.asyncio
async def test_non_streaming_replacement():
    """Test that replacements work on non-streaming responses."""
    from litellm.types.utils import Choices, Message, ModelResponse

    # Create a non-streaming response
    response = ModelResponse(
        id="test-id",
        object="chat.completion",
        created=123456,
        model="test-model",
        choices=[
            Choices(
                index=0,
                message=Message(
                    role="assistant",
                    content="Hello Tokyo, the capital of Japan!",
                ),
                finish_reason="stop",
            )
        ],
    )

    # Create policy with replacements
    policy = SimpleStringReplacementPolicy(
        replacements={
            "Tokyo": "TOKYO",
            "Japan": "JAPAN",
        }
    )

    # Create mock context
    from unittest.mock import Mock

    mock_span = Mock()
    mock_span.add_event = Mock()

    request = Request(
        model="test-model",
        messages=[{"role": "user", "content": "test"}],
    )

    context = PolicyContext(
        call_id="test-call-id",
        span=mock_span,
        request=request,
    )

    # Process through policy
    result = await policy.process_full_response(response, context)

    # Check that replacements were applied
    assert result.choices[0].message.content == "Hello TOKYO, the capital of JAPAN!"


@pytest.mark.asyncio
async def test_tool_call_replacement_disabled_by_default():
    """Test that tool calls are NOT modified when apply_to_tool_calls is False (default)."""
    chunks = load_chunks("anthropic_multiple_tools_chunks.json")
    policy = SimpleStringReplacementPolicy(
        replacements={
            "get_weather": "check_weather",
            "location": "place",
        }
    )

    output = await run_policy_on_stream(policy, chunks)

    # Extract tool calls
    tool_chunks = [
        c for c in output if c.choices and hasattr(c.choices[0].delta, "tool_calls") and c.choices[0].delta.tool_calls
    ]

    # Tool calls should be unchanged (apply_to_tool_calls defaults to False)
    for chunk in tool_chunks:
        tool_call = chunk.choices[0].delta.tool_calls[0]
        # Original names should remain
        assert tool_call["function"]["name"] in ["get_weather", "get_time"]
        # Arguments should contain original "location"
        if tool_call["function"]["name"] == "get_weather":
            args = json.loads(tool_call["function"]["arguments"])
            assert "location" in args


@pytest.mark.asyncio
async def test_tool_call_replacement_enabled():
    """Test that tool calls ARE modified when apply_to_tool_calls is True."""
    chunks = load_chunks("anthropic_multiple_tools_chunks.json")
    policy = SimpleStringReplacementPolicy(
        replacements={
            "get_weather": "check_weather",
            "location": "place",
        },
        apply_to_tool_calls=True,
    )

    output = await run_policy_on_stream(policy, chunks)

    # Extract tool calls
    tool_chunks = [
        c for c in output if c.choices and hasattr(c.choices[0].delta, "tool_calls") and c.choices[0].delta.tool_calls
    ]

    # Check that replacements were applied
    tool_names = [c.choices[0].delta.tool_calls[0]["function"]["name"] for c in tool_chunks]

    # "get_weather" should be replaced with "check_weather"
    assert "check_weather" in tool_names
    assert "get_weather" not in tool_names

    # "get_time" should remain unchanged
    assert "get_time" in tool_names

    # Check that arguments were modified
    for chunk in tool_chunks:
        tool_call = chunk.choices[0].delta.tool_calls[0]
        if tool_call["function"]["name"] == "check_weather":
            args = json.loads(tool_call["function"]["arguments"])
            # "location" should be replaced with "place"
            assert "place" in args
            assert "location" not in args


@pytest.mark.asyncio
async def test_tool_call_replacement_preserves_json():
    """Test that tool call replacements don't break JSON structure."""
    chunks = load_chunks("anthropic_multiple_tools_chunks.json")
    # Replace values inside JSON, not keys
    policy = SimpleStringReplacementPolicy(
        replacements={
            "San Francisco": "NEW_CITY",
        },
        apply_to_tool_calls=True,
    )

    output = await run_policy_on_stream(policy, chunks)

    # Extract tool calls
    tool_chunks = [
        c for c in output if c.choices and hasattr(c.choices[0].delta, "tool_calls") and c.choices[0].delta.tool_calls
    ]

    # All tool calls should have valid JSON arguments
    for chunk in tool_chunks:
        tool_call = chunk.choices[0].delta.tool_calls[0]
        # Should be able to parse JSON without errors
        args = json.loads(tool_call["function"]["arguments"])
        assert isinstance(args, dict)
