"""Unit tests for streaming helper functions."""

import asyncio

import pytest
from litellm.types.utils import ChatCompletionMessageToolCall, ModelResponse

from luthien_proxy.messages import Request
from luthien_proxy.policies import PolicyContext
from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext
from luthien_proxy.policy_core.streaming_utils import (
    get_last_ingress_chunk,
    passthrough_accumulated_chunks,
    passthrough_last_chunk,
    send_chunk,
    send_text,
    send_tool_call,
)


@pytest.fixture
def sample_chunk():
    """Create a sample chunk."""
    return ModelResponse(
        id="test-id",
        object="chat.completion.chunk",
        created=1234567890,
        model="gpt-4",
        choices=[{"index": 0, "delta": {"content": "Hello"}, "finish_reason": None}],
    )


@pytest.fixture
def streaming_context():
    """Create a streaming context."""
    from luthien_proxy.streaming.stream_state import StreamState

    # Create PolicyContext
    policy_ctx = PolicyContext(
        transaction_id="test-123",
        request=Request(model="gpt-4", messages=[]),
    )

    # Create StreamingPolicyContext
    ctx = StreamingPolicyContext(
        policy_ctx=policy_ctx,
        egress_queue=asyncio.Queue(),
        original_streaming_response_state=StreamState(),
        keepalive=lambda: None,  # No-op keepalive for tests
    )
    return ctx


@pytest.mark.asyncio
async def test_send_text(streaming_context):
    """Test sending text chunk to egress."""
    await send_text(streaming_context, "Hello world")

    chunk = await streaming_context.egress_queue.get()
    assert chunk.choices[0]["delta"]["content"] == "Hello world"


@pytest.mark.asyncio
async def test_send_text_empty_raises(streaming_context):
    """Test that sending empty text raises ValueError."""
    with pytest.raises(ValueError, match="text must be non-empty"):
        await send_text(streaming_context, "")


@pytest.mark.asyncio
async def test_send_chunk(streaming_context, sample_chunk):
    """Test sending raw chunk to egress."""
    await send_chunk(streaming_context, sample_chunk)

    chunk = await streaming_context.egress_queue.get()
    assert chunk == sample_chunk


@pytest.mark.asyncio
async def test_get_last_ingress_chunk(streaming_context, sample_chunk):
    """Test getting last ingress chunk."""
    # Empty state
    assert get_last_ingress_chunk(streaming_context) is None

    # Add chunks
    streaming_context.original_streaming_response_state.raw_chunks.append(sample_chunk)
    assert get_last_ingress_chunk(streaming_context) == sample_chunk

    # Add another
    chunk2 = ModelResponse(
        id="test-id-2",
        object="chat.completion.chunk",
        created=1234567890,
        model="gpt-4",
        choices=[{"index": 0, "delta": {"content": " world"}, "finish_reason": None}],
    )
    streaming_context.original_streaming_response_state.raw_chunks.append(chunk2)
    assert get_last_ingress_chunk(streaming_context) == chunk2


@pytest.mark.asyncio
async def test_passthrough_last_chunk(streaming_context, sample_chunk):
    """Test passthrough of last chunk."""
    streaming_context.original_streaming_response_state.raw_chunks.append(sample_chunk)

    await passthrough_last_chunk(streaming_context)

    chunk = await streaming_context.egress_queue.get()
    assert chunk == sample_chunk


@pytest.mark.asyncio
async def test_passthrough_accumulated_chunks(streaming_context):
    """Test passthrough of accumulated chunks."""
    chunks = [
        ModelResponse(
            id=f"test-{i}",
            object="chat.completion.chunk",
            created=1234567890,
            model="gpt-4",
            choices=[{"index": 0, "delta": {"content": f"chunk{i}"}, "finish_reason": None}],
        )
        for i in range(3)
    ]

    # Add all chunks
    for chunk in chunks:
        streaming_context.original_streaming_response_state.raw_chunks.append(chunk)

    # Set emission index to 0
    streaming_context.original_streaming_response_state.last_emission_index = 0

    # Passthrough
    await passthrough_accumulated_chunks(streaming_context)

    # Verify all chunks emitted
    for i in range(3):
        emitted = await streaming_context.egress_queue.get()
        assert emitted.choices[0]["delta"]["content"] == f"chunk{i}"

    # Verify emission index updated
    assert streaming_context.original_streaming_response_state.last_emission_index == 3


@pytest.mark.asyncio
async def test_passthrough_accumulated_chunks_from_middle(streaming_context):
    """Test passthrough only emits chunks since last emission."""
    chunks = [
        ModelResponse(
            id=f"test-{i}",
            object="chat.completion.chunk",
            created=1234567890,
            model="gpt-4",
            choices=[{"index": 0, "delta": {"content": f"chunk{i}"}, "finish_reason": None}],
        )
        for i in range(5)
    ]

    # Add all chunks
    for chunk in chunks:
        streaming_context.original_streaming_response_state.raw_chunks.append(chunk)

    # Set emission index to 2 (already emitted first 2)
    streaming_context.original_streaming_response_state.last_emission_index = 2

    # Passthrough
    await passthrough_accumulated_chunks(streaming_context)

    # Should only emit chunks 2, 3, 4
    for i in range(2, 5):
        emitted = await streaming_context.egress_queue.get()
        assert emitted.choices[0]["delta"]["content"] == f"chunk{i}"

    # Queue should be empty
    assert streaming_context.egress_queue.empty()

    # Verify emission index updated
    assert streaming_context.original_streaming_response_state.last_emission_index == 5


@pytest.mark.asyncio
async def test_send_tool_call(streaming_context):
    """Test sending complete tool call as chunk."""
    tool_call = ChatCompletionMessageToolCall(
        id="call-123",
        type="function",
        function={"name": "get_weather", "arguments": '{"location": "NYC"}'},
    )

    await send_tool_call(streaming_context, tool_call)

    chunk = await streaming_context.egress_queue.get()
    # Tool call is converted to dict format in the chunk
    tool_calls = chunk.choices[0]["delta"]["tool_calls"]
    assert len(tool_calls) == 1
    assert tool_calls[0]["id"] == "call-123"
    assert tool_calls[0]["function"]["name"] == "get_weather"
    assert tool_calls[0]["function"]["arguments"] == '{"location": "NYC"}'
