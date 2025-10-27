"""ABOUTME: Unit tests for SimpleEventBasedPolicy with buffering behavior.
ABOUTME: Tests that content and tool calls are buffered and passed to simplified hooks."""

import asyncio
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from litellm.types.utils import ModelResponse

from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.policies.policy_context import PolicyContext
from luthien_proxy.v2.policies.simple_event_based_policy import SimpleEventBasedPolicy
from luthien_proxy.v2.streaming.stream_blocks import ToolCallStreamBlock

FIXTURE_DIR = Path(__file__).parent / "streaming" / "chunk_fixtures"


def load_chunks(filename: str) -> list[dict]:
    """Load chunk data from JSON file."""
    path = FIXTURE_DIR / filename
    with path.open() as f:
        return json.load(f)


async def simulate_stream(chunks: list[dict]):
    """Simulate async stream from chunk list."""
    for chunk_dict in chunks:
        mr = ModelResponse(**chunk_dict)

        # Fix Pydantic default: restore original finish_reason from dict
        if chunk_dict.get("choices"):
            original_finish = chunk_dict["choices"][0].get("finish_reason")
            if mr.choices and mr.choices[0].finish_reason != original_finish:
                mr.choices[0].finish_reason = original_finish

        yield mr


async def run_policy_on_stream(
    policy: SimpleEventBasedPolicy,
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


class UppercaseContentPolicy(SimpleEventBasedPolicy):
    """Test policy that uppercases content."""

    async def on_response_content(self, content, context, streaming_ctx):
        return content.upper()


class BlockToolCallsPolicy(SimpleEventBasedPolicy):
    """Test policy that blocks all tool calls."""

    async def on_response_tool_call(self, tool_call, context, streaming_ctx):
        return None  # Block all tool calls


class RenameToolPolicy(SimpleEventBasedPolicy):
    """Test policy that renames tool calls."""

    async def on_response_tool_call(self, tool_call, context, streaming_ctx):
        # Rename get_weather to check_weather
        if tool_call.name == "get_weather":
            tool_call.name = "check_weather"
        return tool_call


class RecordingPolicy(SimpleEventBasedPolicy):
    """Test policy that records what hooks were called."""

    def __init__(self):
        super().__init__()
        self.calls = []

    async def on_response_content(self, content, context, streaming_ctx):
        self.calls.append(("content", content))
        return content

    async def on_response_tool_call(self, tool_call, context, streaming_ctx):
        self.calls.append(("tool_call", tool_call.name, tool_call.arguments))
        return tool_call


@pytest.mark.asyncio
async def test_simple_policy_buffers_content():
    """Test that SimpleEventBasedPolicy buffers content and sends it once."""
    chunks = load_chunks("no_tools_used_chunks.json")
    policy = SimpleEventBasedPolicy()
    output = await run_policy_on_stream(policy, chunks)

    # Should get exactly one content chunk (not multiple deltas)
    content_chunks = [c for c in output if c.choices and c.choices[0].delta.content]
    assert len(content_chunks) == 1

    # Content should match original
    assert content_chunks[0].choices[0].delta.content.startswith("Here are the major cities")


@pytest.mark.asyncio
async def test_uppercase_content_policy():
    """Test policy that transforms content."""
    chunks = load_chunks("no_tools_used_chunks.json")
    policy = UppercaseContentPolicy()
    output = await run_policy_on_stream(policy, chunks)

    # Get content chunk
    content_chunks = [c for c in output if c.choices and c.choices[0].delta.content]
    assert len(content_chunks) == 1

    # Content should be uppercased
    content = content_chunks[0].choices[0].delta.content
    assert content.isupper()
    assert content.startswith("HERE ARE THE MAJOR CITIES")


@pytest.mark.asyncio
async def test_simple_policy_buffers_tool_calls():
    """Test that SimpleEventBasedPolicy buffers tool calls and sends them once."""
    chunks = load_chunks("gpt_multiple_tools_chunks.json")
    policy = SimpleEventBasedPolicy()
    output = await run_policy_on_stream(policy, chunks)

    # Count chunks with tool_calls
    tool_chunks = [
        c for c in output if c.choices and hasattr(c.choices[0].delta, "tool_calls") and c.choices[0].delta.tool_calls
    ]

    # Should get exactly 4 tool call chunks (one per tool call, not deltas)
    assert len(tool_chunks) == 4

    # Each chunk should have complete tool call data
    for chunk in tool_chunks:
        tool_call = chunk.choices[0].delta.tool_calls[0]
        assert tool_call["function"]["name"] in ["get_weather", "get_time"]
        # Arguments should be complete JSON
        args = json.loads(tool_call["function"]["arguments"])
        assert "location" in args


@pytest.mark.asyncio
async def test_block_tool_calls_policy():
    """Test policy that blocks all tool calls."""
    chunks = load_chunks("gpt_multiple_tools_chunks.json")
    policy = BlockToolCallsPolicy()
    output = await run_policy_on_stream(policy, chunks)

    # Should not have any tool call chunks
    tool_chunks = [
        c for c in output if c.choices and hasattr(c.choices[0].delta, "tool_calls") and c.choices[0].delta.tool_calls
    ]
    assert len(tool_chunks) == 0


@pytest.mark.asyncio
async def test_rename_tool_policy():
    """Test policy that modifies tool calls."""
    chunks = load_chunks("gpt_multiple_tools_chunks.json")
    policy = RenameToolPolicy()
    output = await run_policy_on_stream(policy, chunks)

    # Get tool call chunks
    tool_chunks = [
        c for c in output if c.choices and hasattr(c.choices[0].delta, "tool_calls") and c.choices[0].delta.tool_calls
    ]

    # Check that get_weather was renamed to check_weather
    tool_names = [c.choices[0].delta.tool_calls[0]["function"]["name"] for c in tool_chunks]
    assert "check_weather" in tool_names
    assert "get_weather" not in tool_names
    # get_time should remain unchanged
    assert "get_time" in tool_names


@pytest.mark.asyncio
async def test_recording_policy_content_and_tools():
    """Test that hooks are called with complete data."""
    chunks = load_chunks("anthropic_multiple_tools_chunks.json")
    policy = RecordingPolicy()
    await run_policy_on_stream(policy, chunks)

    # Should have recorded content + 4 tool calls
    assert len(policy.calls) == 5

    # First call is content
    assert policy.calls[0][0] == "content"
    assert "weather and current time" in policy.calls[0][1]

    # Next 4 are tool calls
    for i in range(1, 5):
        assert policy.calls[i][0] == "tool_call"
        assert policy.calls[i][1] in ["get_weather", "get_time"]
        # Arguments should be complete JSON
        args = json.loads(policy.calls[i][2])
        assert "location" in args


@pytest.mark.asyncio
async def test_recording_policy_tools_only():
    """Test hooks with tool calls but no content."""
    chunks = load_chunks("gpt_multiple_tools_chunks.json")
    policy = RecordingPolicy()
    await run_policy_on_stream(policy, chunks)

    # Should have recorded 4 tool calls, no content
    assert len(policy.calls) == 4

    # All are tool calls
    for call in policy.calls:
        assert call[0] == "tool_call"
        assert call[1] in ["get_weather", "get_time"]


@pytest.mark.asyncio
async def test_recording_policy_content_only():
    """Test hooks with content but no tool calls."""
    chunks = load_chunks("no_tools_used_chunks.json")
    policy = RecordingPolicy()
    await run_policy_on_stream(policy, chunks)

    # Should have recorded content only
    assert len(policy.calls) == 1
    assert policy.calls[0][0] == "content"
    assert policy.calls[0][1].startswith("Here are the major cities")


@pytest.mark.asyncio
async def test_on_request_passthrough():
    """Test that on_request is called and defaults to passthrough."""
    request = Request(
        model="claude-3-opus-20240229",
        messages=[{"role": "user", "content": "test"}],
    )
    policy = SimpleEventBasedPolicy()

    # Create mock span
    mock_span = Mock()
    mock_span.add_event = Mock()

    context = PolicyContext(
        call_id="test-call-id",
        span=mock_span,
        request=request,
    )

    result = await policy.process_request(request, context)
    assert result == request


@pytest.mark.asyncio
async def test_empty_content_not_sent():
    """Test that empty content is not sent to client."""

    class EmptyContentPolicy(SimpleEventBasedPolicy):
        async def on_response_content(self, content, context, streaming_ctx):
            return ""  # Return empty content

    chunks = load_chunks("no_tools_used_chunks.json")
    policy = EmptyContentPolicy()
    output = await run_policy_on_stream(policy, chunks)

    # Should not have any content chunks
    content_chunks = [c for c in output if c.choices and c.choices[0].delta.content]
    assert len(content_chunks) == 0


@pytest.mark.asyncio
async def test_tool_call_block_has_complete_data():
    """Test that tool call blocks passed to hook have complete data."""

    class InspectToolCallPolicy(SimpleEventBasedPolicy):
        def __init__(self):
            super().__init__()
            self.inspected_blocks = []

        async def on_response_tool_call(self, tool_call, context, streaming_ctx):
            # Verify block has complete data
            assert tool_call.name != ""
            assert tool_call.arguments != ""
            assert tool_call.id != ""
            # Arguments should be valid JSON
            json.loads(tool_call.arguments)
            self.inspected_blocks.append(tool_call)
            return tool_call

    chunks = load_chunks("anthropic_multiple_tools_chunks.json")
    policy = InspectToolCallPolicy()
    await run_policy_on_stream(policy, chunks)

    # Should have inspected 4 tool calls
    assert len(policy.inspected_blocks) == 4

    # All should be complete ToolCallStreamBlock instances
    for block in policy.inspected_blocks:
        assert isinstance(block, ToolCallStreamBlock)
        assert block.is_complete
