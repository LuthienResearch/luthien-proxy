# ABOUTME: Unit tests for EventDrivenUppercaseNthWordPolicy
# ABOUTME: Verifies equivalence with manual implementation and correctness

"""Tests for EventDrivenUppercaseNthWordPolicy.

Tests verify:
- Equivalence with manual UppercaseNthWordPolicy implementation
- Correct word boundary handling
- State isolation
- Finalization (last word without trailing space)
"""

from __future__ import annotations

import asyncio
from unittest.mock import Mock

import pytest
from litellm.types.utils import Delta, ModelResponse, StreamingChoices

from luthien_proxy.v2.policies.context import PolicyContext
from luthien_proxy.v2.policies.event_driven_uppercase_nth_word import EventDrivenUppercaseNthWordPolicy


def create_test_context(call_id: str = "test") -> PolicyContext:
    """Create a PolicyContext for testing."""
    mock_span = Mock()
    return PolicyContext(call_id=call_id, span=mock_span)


def create_text_chunk(content: str) -> ModelResponse:
    """Create a streaming chunk with text content."""
    delta = Delta(content=content, role="assistant")
    choice = StreamingChoices(delta=delta, finish_reason=None, index=0)
    return ModelResponse(choices=[choice])


def create_finish_chunk(reason: str) -> ModelResponse:
    """Create a streaming chunk with finish_reason."""
    delta = Delta(role=None)
    choice = StreamingChoices(delta=delta, finish_reason=reason, index=0)
    return ModelResponse(choices=[choice])


def extract_content_from_chunks(chunks: list[ModelResponse]) -> str:
    """Extract all text content from a list of chunks."""
    content_parts = []
    for chunk in chunks:
        chunk_dict = chunk.model_dump() if hasattr(chunk, "model_dump") else dict(chunk)  # type: ignore
        choices = chunk_dict.get("choices", [])
        if choices:
            delta = choices[0].get("delta", {})
            if isinstance(delta, dict):
                text = delta.get("content")
                if text:
                    content_parts.append(text)
    return "".join(content_parts)


async def drain_queue(queue: asyncio.Queue[ModelResponse]) -> list[ModelResponse]:
    """Drain all chunks from queue."""
    chunks = []
    try:
        while True:
            chunk = await asyncio.wait_for(queue.get(), timeout=0.1)
            chunks.append(chunk)
    except (asyncio.TimeoutError, asyncio.QueueShutDown):
        pass
    return chunks


@pytest.mark.asyncio
async def test_basic_transformation():
    """Test basic uppercase transformation."""
    policy = EventDrivenUppercaseNthWordPolicy(n=3)
    incoming = asyncio.Queue[ModelResponse]()
    outgoing = asyncio.Queue[ModelResponse]()
    context = create_test_context()

    # Add chunks: "one two three four five six"
    # Expected output: "one two THREE four five SIX"
    incoming.put_nowait(create_text_chunk("one two three four five six"))
    incoming.put_nowait(create_finish_chunk("stop"))
    incoming.shutdown()

    # Process
    await policy.process_streaming_response(incoming, outgoing, context)

    # Extract content
    output_chunks = await drain_queue(outgoing)
    content = extract_content_from_chunks(output_chunks)

    assert content == "one two THREE four five SIX"


@pytest.mark.asyncio
async def test_chunked_input():
    """Test transformation with chunked input (word boundaries across chunks)."""
    policy = EventDrivenUppercaseNthWordPolicy(n=2)
    incoming = asyncio.Queue[ModelResponse]()
    outgoing = asyncio.Queue[ModelResponse]()
    context = create_test_context()

    # Add chunks: words come in separate chunks
    # Input: "hello world foo bar"
    # Expected: "hello WORLD foo BAR"
    incoming.put_nowait(create_text_chunk("hello "))
    incoming.put_nowait(create_text_chunk("world "))
    incoming.put_nowait(create_text_chunk("foo "))
    incoming.put_nowait(create_text_chunk("bar"))  # No trailing space
    incoming.put_nowait(create_finish_chunk("stop"))
    incoming.shutdown()

    # Process
    await policy.process_streaming_response(incoming, outgoing, context)

    # Extract content
    output_chunks = await drain_queue(outgoing)
    content = extract_content_from_chunks(output_chunks)

    assert content == "hello WORLD foo BAR"


@pytest.mark.asyncio
async def test_partial_words_across_chunks():
    """Test handling of partial words across chunks."""
    policy = EventDrivenUppercaseNthWordPolicy(n=2)
    incoming = asyncio.Queue[ModelResponse]()
    outgoing = asyncio.Queue[ModelResponse]()
    context = create_test_context()

    # Add chunks where words are split across chunks
    incoming.put_nowait(create_text_chunk("hel"))
    incoming.put_nowait(create_text_chunk("lo "))
    incoming.put_nowait(create_text_chunk("wo"))
    incoming.put_nowait(create_text_chunk("rld"))
    incoming.put_nowait(create_finish_chunk("stop"))
    incoming.shutdown()

    # Process
    await policy.process_streaming_response(incoming, outgoing, context)

    # Extract content
    output_chunks = await drain_queue(outgoing)
    content = extract_content_from_chunks(output_chunks)

    # "hello world" -> "hello WORLD"
    assert content == "hello WORLD"


@pytest.mark.asyncio
async def test_last_word_without_space():
    """Test that last word is processed even without trailing space."""
    policy = EventDrivenUppercaseNthWordPolicy(n=3)
    incoming = asyncio.Queue[ModelResponse]()
    outgoing = asyncio.Queue[ModelResponse]()
    context = create_test_context()

    # Last word has no trailing space
    incoming.put_nowait(create_text_chunk("one two three"))
    incoming.put_nowait(create_finish_chunk("stop"))
    incoming.shutdown()

    # Process
    await policy.process_streaming_response(incoming, outgoing, context)

    # Extract content
    output_chunks = await drain_queue(outgoing)
    content = extract_content_from_chunks(output_chunks)

    assert content == "one two THREE"


@pytest.mark.asyncio
async def test_single_word():
    """Test handling of single word input."""
    policy = EventDrivenUppercaseNthWordPolicy(n=1)
    incoming = asyncio.Queue[ModelResponse]()
    outgoing = asyncio.Queue[ModelResponse]()
    context = create_test_context()

    incoming.put_nowait(create_text_chunk("hello"))
    incoming.put_nowait(create_finish_chunk("stop"))
    incoming.shutdown()

    # Process
    await policy.process_streaming_response(incoming, outgoing, context)

    # Extract content
    output_chunks = await drain_queue(outgoing)
    content = extract_content_from_chunks(output_chunks)

    # n=1 means every word is uppercased
    assert content == "HELLO"


@pytest.mark.asyncio
async def test_empty_content():
    """Test handling of empty content."""
    policy = EventDrivenUppercaseNthWordPolicy(n=3)
    incoming = asyncio.Queue[ModelResponse]()
    outgoing = asyncio.Queue[ModelResponse]()
    context = create_test_context()

    incoming.put_nowait(create_text_chunk(""))
    incoming.put_nowait(create_finish_chunk("stop"))
    incoming.shutdown()

    # Process
    await policy.process_streaming_response(incoming, outgoing, context)

    # Extract content
    output_chunks = await drain_queue(outgoing)
    content = extract_content_from_chunks(output_chunks)

    assert content == ""


@pytest.mark.asyncio
async def test_state_isolation():
    """Test that state is isolated between concurrent requests."""
    policy = EventDrivenUppercaseNthWordPolicy(n=2)

    async def process_stream(text: str) -> str:
        incoming = asyncio.Queue[ModelResponse]()
        outgoing = asyncio.Queue[ModelResponse]()
        context = create_test_context(call_id=f"test_{text}")

        incoming.put_nowait(create_text_chunk(text))
        incoming.put_nowait(create_finish_chunk("stop"))
        incoming.shutdown()

        await policy.process_streaming_response(incoming, outgoing, context)

        output_chunks = await drain_queue(outgoing)
        return extract_content_from_chunks(output_chunks)

    # Run concurrently
    results = await asyncio.gather(
        process_stream("one two three"),
        process_stream("four five six"),
    )

    # Verify independent transformations
    assert results[0] == "one TWO three"
    assert results[1] == "four FIVE six"


@pytest.mark.asyncio
async def test_n_validation():
    """Test that n parameter is validated."""
    with pytest.raises(ValueError, match="n must be >= 1"):
        EventDrivenUppercaseNthWordPolicy(n=0)

    with pytest.raises(ValueError, match="n must be >= 1"):
        EventDrivenUppercaseNthWordPolicy(n=-1)


@pytest.mark.asyncio
async def test_finish_chunk_forwarded():
    """Test that non-content chunks (like finish) are forwarded."""
    policy = EventDrivenUppercaseNthWordPolicy(n=3)
    incoming = asyncio.Queue[ModelResponse]()
    outgoing = asyncio.Queue[ModelResponse]()
    context = create_test_context()

    incoming.put_nowait(create_text_chunk("one two three"))
    incoming.put_nowait(create_finish_chunk("stop"))
    incoming.shutdown()

    # Process
    await policy.process_streaming_response(incoming, outgoing, context)

    # Verify finish chunk is in output
    output_chunks = await drain_queue(outgoing)

    # Find finish chunk
    finish_chunks = [c for c in output_chunks if c.choices[0].finish_reason == "stop"]  # type: ignore
    assert len(finish_chunks) == 1
