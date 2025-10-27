"""ABOUTME: Unit tests for SimpleBlockedContentPolicy.
ABOUTME: Tests regex-based content blocking with various patterns and configurations."""

import asyncio
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from litellm.types.utils import ModelResponse

from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.policies.policy_context import PolicyContext
from luthien_proxy.v2.policies.simple_blocked_content import SimpleBlockedContentPolicy

FIXTURE_DIR = Path(__file__).parent / "streaming" / "chunk_fixtures"


def load_chunks(filename: str) -> list[dict]:
    """Load chunk data from JSON file."""
    path = FIXTURE_DIR / filename
    with path.open() as f:
        return json.load(f)


async def run_policy_on_stream(
    policy: SimpleBlockedContentPolicy,
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
async def test_no_patterns_passes_through():
    """Test that policy with no patterns passes content through unchanged."""
    chunks = load_chunks("no_tools_used_chunks.json")
    policy = SimpleBlockedContentPolicy(blocked_patterns=[])
    output = await run_policy_on_stream(policy, chunks)

    # Content should be unchanged
    content = extract_content_from_chunks(output)
    assert content.startswith("Here are the major cities")


@pytest.mark.asyncio
async def test_block_simple_word():
    """Test blocking content containing a simple word."""
    chunks = load_chunks("no_tools_used_chunks.json")
    policy = SimpleBlockedContentPolicy(
        blocked_patterns=["password"],
        replacement_message="[BLOCKED]",
    )
    output = await run_policy_on_stream(policy, chunks)

    # Original content doesn't contain "password", should pass through
    content = extract_content_from_chunks(output)
    assert content.startswith("Here are the major cities")
    assert "[BLOCKED]" not in content


@pytest.mark.asyncio
async def test_block_matching_content():
    """Test blocking content that matches a pattern."""
    chunks = load_chunks("no_tools_used_chunks.json")
    # "Tokyo" appears in the content
    policy = SimpleBlockedContentPolicy(
        blocked_patterns=["Tokyo"],
        replacement_message="[BLOCKED: City name detected]",
    )
    output = await run_policy_on_stream(policy, chunks)

    # Content should be replaced
    content = extract_content_from_chunks(output)
    assert content == "[BLOCKED: City name detected]"
    assert "Tokyo" not in content


@pytest.mark.asyncio
async def test_case_insensitive_matching():
    """Test that pattern matching is case insensitive."""
    chunks = load_chunks("no_tools_used_chunks.json")
    # Match "tokyo" (lowercase) against "Tokyo" (in content)
    policy = SimpleBlockedContentPolicy(
        blocked_patterns=["tokyo"],
        replacement_message="[BLOCKED]",
    )
    output = await run_policy_on_stream(policy, chunks)

    # Should match despite different case
    content = extract_content_from_chunks(output)
    assert content == "[BLOCKED]"


@pytest.mark.asyncio
async def test_regex_pattern_ssn():
    """Test blocking SSN pattern."""
    from litellm.types.utils import Choices, Message, ModelResponse

    # Create a non-streaming response with SSN
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
                    content="Your SSN is 123-45-6789 and your account is ready.",
                ),
                finish_reason="stop",
            )
        ],
    )

    # Create policy with SSN pattern
    policy = SimpleBlockedContentPolicy(
        blocked_patterns=[r"\b\d{3}-\d{2}-\d{4}\b"],  # SSN pattern
        replacement_message="[BLOCKED: SSN detected]",
    )

    # Create mock context
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

    # Check that content was blocked
    assert result.choices[0].message.content == "[BLOCKED: SSN detected]"


@pytest.mark.asyncio
async def test_regex_pattern_credit_card():
    """Test blocking credit card pattern."""
    from litellm.types.utils import Choices, Message, ModelResponse

    # Create a non-streaming response with credit card
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
                    content="Your card number is 1234567890123456.",
                ),
                finish_reason="stop",
            )
        ],
    )

    # Create policy with credit card pattern (16 digits)
    policy = SimpleBlockedContentPolicy(
        blocked_patterns=[r"\b\d{16}\b"],
        replacement_message="[BLOCKED: Credit card detected]",
    )

    # Create mock context
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

    # Check that content was blocked
    assert result.choices[0].message.content == "[BLOCKED: Credit card detected]"


@pytest.mark.asyncio
async def test_multiple_patterns():
    """Test blocking with multiple patterns."""
    from litellm.types.utils import Choices, Message, ModelResponse

    # Create responses with different patterns
    responses = [
        ModelResponse(
            id="test-1",
            object="chat.completion",
            created=123456,
            model="test-model",
            choices=[
                Choices(
                    index=0,
                    message=Message(role="assistant", content="Your password is secret123"),
                    finish_reason="stop",
                )
            ],
        ),
        ModelResponse(
            id="test-2",
            object="chat.completion",
            created=123456,
            model="test-model",
            choices=[
                Choices(
                    index=0,
                    message=Message(role="assistant", content="Execute this code: rm -rf /"),
                    finish_reason="stop",
                )
            ],
        ),
    ]

    # Create policy with multiple patterns
    policy = SimpleBlockedContentPolicy(
        blocked_patterns=["password", "execute"],
        replacement_message="[BLOCKED]",
    )

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

    # Both should be blocked
    for response in responses:
        result = await policy.process_full_response(response, context)
        assert result.choices[0].message.content == "[BLOCKED]"


@pytest.mark.asyncio
async def test_tool_call_blocking_enabled():
    """Test that tool calls are blocked when they match patterns."""
    chunks = load_chunks("anthropic_multiple_tools_chunks.json")

    # Block any tool containing "weather"
    policy = SimpleBlockedContentPolicy(
        blocked_patterns=["weather"],
        replacement_message="[BLOCKED: Weather tool not allowed]",
        block_tool_calls=True,
    )

    output = await run_policy_on_stream(policy, chunks)

    # Check output - tool call should be replaced with text
    content = extract_content_from_chunks(output)
    assert "[BLOCKED: Weather tool not allowed]" in content

    # Tool call chunks should not appear
    tool_chunks = [
        c for c in output if c.choices and hasattr(c.choices[0].delta, "tool_calls") and c.choices[0].delta.tool_calls
    ]

    # get_weather should be blocked, but get_time should pass through
    tool_names = []
    for chunk in tool_chunks:
        if chunk.choices[0].delta.tool_calls:
            tool_call = chunk.choices[0].delta.tool_calls[0]
            if "name" in tool_call["function"] and tool_call["function"]["name"]:
                tool_names.append(tool_call["function"]["name"])

    # Weather tool should not appear
    assert "get_weather" not in tool_names
    # Time tool should still appear
    assert "get_time" in tool_names


@pytest.mark.asyncio
async def test_tool_call_blocking_disabled():
    """Test that tool calls pass through when block_tool_calls is False."""
    chunks = load_chunks("anthropic_multiple_tools_chunks.json")

    # Pattern matches but blocking is disabled
    policy = SimpleBlockedContentPolicy(
        blocked_patterns=["weather"],
        replacement_message="[BLOCKED]",
        block_tool_calls=False,
    )

    output = await run_policy_on_stream(policy, chunks)

    # Tool calls should pass through unchanged
    tool_chunks = [
        c for c in output if c.choices and hasattr(c.choices[0].delta, "tool_calls") and c.choices[0].delta.tool_calls
    ]

    tool_names = []
    for chunk in tool_chunks:
        if chunk.choices[0].delta.tool_calls:
            tool_call = chunk.choices[0].delta.tool_calls[0]
            if "name" in tool_call["function"] and tool_call["function"]["name"]:
                tool_names.append(tool_call["function"]["name"])

    # Both tools should appear
    assert "get_weather" in tool_names
    assert "get_time" in tool_names


@pytest.mark.asyncio
async def test_tool_call_arguments_blocking():
    """Test blocking tool calls based on argument patterns."""
    chunks = load_chunks("anthropic_multiple_tools_chunks.json")

    # Block based on argument content (Tokyo appears in arguments)
    policy = SimpleBlockedContentPolicy(
        blocked_patterns=["Tokyo"],
        replacement_message="[BLOCKED: Location not allowed]",
        block_tool_calls=True,
    )

    output = await run_policy_on_stream(policy, chunks)

    # Check that blocked message appears
    content = extract_content_from_chunks(output)
    assert "[BLOCKED: Location not allowed]" in content


@pytest.mark.asyncio
async def test_invalid_regex_raises_error():
    """Test that invalid regex patterns raise ValueError."""
    with pytest.raises(ValueError, match="Invalid regex pattern"):
        SimpleBlockedContentPolicy(
            blocked_patterns=["[invalid(regex"],
            replacement_message="[BLOCKED]",
        )


@pytest.mark.asyncio
async def test_event_emission():
    """Test that policy emits events when content is blocked."""
    from litellm.types.utils import Choices, Message, ModelResponse

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
                    content="Your password is secret123",
                ),
                finish_reason="stop",
            )
        ],
    )

    policy = SimpleBlockedContentPolicy(
        blocked_patterns=["password"],
        replacement_message="[BLOCKED]",
    )

    # Create mock span to track events
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

    await policy.process_full_response(response, context)

    # Check that an event was emitted
    mock_span.add_event.assert_called()

    # Find the content_blocked event
    calls = [call for call in mock_span.add_event.call_args_list]
    event_types = [call[0][0] for call in calls]
    assert "policy.content_blocked" in event_types


@pytest.mark.asyncio
async def test_default_replacement_message():
    """Test default replacement message."""
    from litellm.types.utils import Choices, Message, ModelResponse

    response = ModelResponse(
        id="test-id",
        object="chat.completion",
        created=123456,
        model="test-model",
        choices=[
            Choices(
                index=0,
                message=Message(role="assistant", content="password"),
                finish_reason="stop",
            )
        ],
    )

    # Don't specify replacement_message, use default
    policy = SimpleBlockedContentPolicy(blocked_patterns=["password"])

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

    result = await policy.process_full_response(response, context)

    # Should use default message
    assert result.choices[0].message.content == "[BLOCKED: Content policy violation]"


@pytest.mark.asyncio
async def test_empty_content_passes_through():
    """Test that empty content is not blocked."""
    from litellm.types.utils import Choices, Message, ModelResponse

    response = ModelResponse(
        id="test-id",
        object="chat.completion",
        created=123456,
        model="test-model",
        choices=[
            Choices(
                index=0,
                message=Message(role="assistant", content=""),
                finish_reason="stop",
            )
        ],
    )

    policy = SimpleBlockedContentPolicy(
        blocked_patterns=["anything"],
        replacement_message="[BLOCKED]",
    )

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

    result = await policy.process_full_response(response, context)

    # Empty content should remain empty
    assert result.choices[0].message.content == ""


@pytest.mark.asyncio
async def test_complex_regex_patterns():
    """Test complex regex patterns with special characters."""
    from litellm.types.utils import Choices, Message, ModelResponse

    test_cases = [
        # Email pattern
        (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "Contact me at user@example.com", True),
        # IP address pattern
        (r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "Server IP is 192.168.1.1", True),
        # URL pattern
        (r"https?://[^\s]+", "Visit https://example.com for more", True),
        # Should not match - looking for exactly 10 digits but content has none
        (r"\b\d{10}\b", "This text has no ten digit numbers", False),
    ]

    for pattern, content, should_block in test_cases:
        response = ModelResponse(
            id="test-id",
            object="chat.completion",
            created=123456,
            model="test-model",
            choices=[
                Choices(
                    index=0,
                    message=Message(role="assistant", content=content),
                    finish_reason="stop",
                )
            ],
        )

        policy = SimpleBlockedContentPolicy(
            blocked_patterns=[pattern],
            replacement_message="[BLOCKED]",
        )

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

        result = await policy.process_full_response(response, context)

        if should_block:
            assert result.choices[0].message.content == "[BLOCKED]", f"Pattern {pattern} should have blocked: {content}"
        else:
            assert result.choices[0].message.content == content, f"Pattern {pattern} should not have blocked: {content}"
