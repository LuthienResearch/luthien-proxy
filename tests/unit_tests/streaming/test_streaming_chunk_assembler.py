"""ABOUTME: Unit tests for StreamingChunkAssembler using real streaming chunks.
ABOUTME: Tests block detection, transitions, and completion using saved chunk data."""

import json
from pathlib import Path

import pytest
from litellm.types.utils import Delta, ModelResponse

from luthien_proxy.streaming.stream_blocks import (
    ContentStreamBlock,
    ToolCallStreamBlock,
)
from luthien_proxy.streaming.stream_state import StreamState
from luthien_proxy.streaming.streaming_chunk_assembler import StreamingChunkAssembler

FIXTURE_DIR = Path(__file__).parent / "chunk_fixtures"


class ChunkRecorder:
    """Records chunk callbacks for testing."""

    def __init__(self):
        self.calls = []

    async def on_chunk(self, chunk, state: StreamState, context):
        """Record each callback invocation."""
        self.calls.append(
            {
                "chunk_num": len(self.calls) + 1,
                "blocks": len(state.blocks),
                "current_block_type": (type(state.current_block).__name__ if state.current_block else None),
                "just_completed_type": (type(state.just_completed).__name__ if state.just_completed else None),
                "just_completed_id": (state.just_completed.id if state.just_completed else None),
                "finish_reason": state.finish_reason,
                # Deep copy state for assertions
                "state_snapshot": {
                    "blocks": [
                        {
                            "type": type(b).__name__,
                            "id": b.id,
                            "is_complete": b.is_complete,
                            "content": b.content if isinstance(b, ContentStreamBlock) else None,
                            "name": b.name if isinstance(b, ToolCallStreamBlock) else None,
                            "arguments": (b.arguments if isinstance(b, ToolCallStreamBlock) else None),
                            "index": b.index if isinstance(b, ToolCallStreamBlock) else None,
                        }
                        for b in state.blocks
                    ],
                },
            }
        )


def load_chunks(filename: str) -> list[ModelResponse]:
    """Load streaming ModelResponse objects from JSON fixture file.

    Returns a list of LiteLLM ModelResponse objects that represent streaming chunks
    as they would appear in practice from the LLM API.
    """
    path = FIXTURE_DIR / filename
    with path.open() as f:
        chunk_dicts = json.load(f)

    responses = []
    for chunk_dict in chunk_dicts:
        # Convert delta dicts to Delta objects for proper typing
        if chunk_dict.get("choices"):
            for choice in chunk_dict["choices"]:
                if choice.get("delta") and isinstance(choice["delta"], dict):
                    choice["delta"] = Delta(**choice["delta"])

        # Create ModelResponse from dict
        mr = ModelResponse.model_validate(chunk_dict)

        # Fix Pydantic default: restore original finish_reason from dict
        # ModelResponse sets finish_reason='stop' as default even when None in JSON
        if chunk_dict.get("choices"):
            original_finish = chunk_dict["choices"][0].get("finish_reason")
            if mr.choices and mr.choices[0].finish_reason != original_finish:
                mr.choices[0].finish_reason = original_finish

        responses.append(mr)

    return responses


async def simulate_stream(chunks: list[ModelResponse]):
    """Simulate async stream from ModelResponse list.

    Yields each ModelResponse object as it would be received from an async stream.
    """
    for mr in chunks:
        yield mr


@pytest.mark.asyncio
async def test_content_only_response():
    """Test response with only content, no tool calls."""
    chunks = load_chunks("no_tools_used_chunks.json")
    recorder = ChunkRecorder()
    processor = StreamingChunkAssembler(on_chunk_callback=recorder.on_chunk)

    await processor.process(simulate_stream(chunks), context=None)

    # Should have exactly one block (content)
    assert len(processor.state.blocks) == 1
    assert isinstance(processor.state.blocks[0], ContentStreamBlock)

    # Content block should be complete
    assert processor.state.blocks[0].is_complete
    assert processor.state.blocks[0].content.startswith("Here are the major cities")

    # Should have finish_reason
    assert processor.state.finish_reason == "stop"

    # Check that content block completed exactly once
    completions = [c for c in recorder.calls if c["just_completed_type"] == "ContentStreamBlock"]
    assert len(completions) == 1


@pytest.mark.asyncio
async def test_anthropic_multiple_tool_calls():
    """Test Anthropic response with content then multiple tool calls."""
    chunks = load_chunks("anthropic_multiple_tools_chunks.json")
    recorder = ChunkRecorder()
    processor = StreamingChunkAssembler(on_chunk_callback=recorder.on_chunk)

    await processor.process(simulate_stream(chunks), context=None)

    # Should have 5 blocks: content + 4 tool calls
    assert len(processor.state.blocks) == 5

    # First block is content
    assert isinstance(processor.state.blocks[0], ContentStreamBlock)
    assert processor.state.blocks[0].is_complete
    assert "weather and current time" in processor.state.blocks[0].content

    # Next 4 blocks are tool calls
    for i in range(1, 5):
        block = processor.state.blocks[i]
        assert isinstance(block, ToolCallStreamBlock)
        assert block.is_complete
        assert block.index == i - 1  # Indices 0, 1, 2, 3

    # Check tool call names
    tool_names = [processor.state.blocks[i].name for i in range(1, 5)]
    assert "get_weather" in tool_names
    assert "get_time" in tool_names

    # Check that each tool call arguments is valid JSON
    for i in range(1, 5):
        block = processor.state.blocks[i]
        args = json.loads(block.arguments)
        assert "location" in args

    # Should have finish_reason
    assert processor.state.finish_reason == "tool_calls"

    # Check block completions
    content_completions = [c for c in recorder.calls if c["just_completed_type"] == "ContentStreamBlock"]
    assert len(content_completions) == 1

    tool_completions = [c for c in recorder.calls if c["just_completed_type"] == "ToolCallStreamBlock"]
    # 4 tool calls complete
    assert len(tool_completions) == 4


@pytest.mark.asyncio
async def test_gpt_multiple_tool_calls():
    """Test GPT response with multiple tool calls (no content)."""
    chunks = load_chunks("gpt_multiple_tools_chunks.json")
    recorder = ChunkRecorder()
    processor = StreamingChunkAssembler(on_chunk_callback=recorder.on_chunk)

    await processor.process(simulate_stream(chunks), context=None)

    # Should have 4 blocks (no content, just tool calls)
    assert len(processor.state.blocks) == 4

    # All blocks are tool calls
    for i, block in enumerate(processor.state.blocks):
        assert isinstance(block, ToolCallStreamBlock)
        assert block.is_complete
        assert block.index == i

    # Check tool call data
    for block in processor.state.blocks:
        assert block.name in ["get_weather", "get_time"]
        args = json.loads(block.arguments)
        assert "location" in args

    # Should have finish_reason
    assert processor.state.finish_reason == "tool_calls"

    # No content block completions
    content_completions = [c for c in recorder.calls if c["just_completed_type"] == "ContentStreamBlock"]
    assert len(content_completions) == 0

    # 4 tool call completions
    tool_completions = [c for c in recorder.calls if c["just_completed_type"] == "ToolCallStreamBlock"]
    assert len(tool_completions) == 4


@pytest.mark.asyncio
async def test_anthropic_extended_thinking():
    """Test Anthropic response with long content then tool calls."""
    chunks = load_chunks("anthropic_extended_thinking_chunks.json")
    recorder = ChunkRecorder()
    processor = StreamingChunkAssembler(on_chunk_callback=recorder.on_chunk)

    await processor.process(simulate_stream(chunks), context=None)

    # Should have 3 blocks: content + 2 tool calls
    assert len(processor.state.blocks) == 3

    # First block is content (long)
    content_block = processor.state.blocks[0]
    assert isinstance(content_block, ContentStreamBlock)
    assert content_block.is_complete
    assert len(content_block.content) > 1000  # Extended thinking
    assert "Tokyo" in content_block.content
    assert "Kyoto" in content_block.content

    # Next 2 blocks are tool calls
    for i in range(1, 3):
        block = processor.state.blocks[i]
        assert isinstance(block, ToolCallStreamBlock)
        assert block.is_complete
        assert block.name == "get_weather"

    # Check arguments
    tc1_args = json.loads(processor.state.blocks[1].arguments)
    tc2_args = json.loads(processor.state.blocks[2].arguments)
    assert "Tokyo" in tc1_args["location"]
    assert "Kyoto" in tc2_args["location"]

    assert processor.state.finish_reason == "tool_calls"


@pytest.mark.asyncio
async def test_empty_content_stripped():
    """Test that empty content fields are stripped from tool call chunks."""
    chunks = load_chunks("anthropic_multiple_tools_chunks.json")

    # Track chunks passed to callback
    chunks_received = []

    async def track_chunks(chunk, state, context):
        # Access delta directly from the chunk, not via model_dump()
        # (model_dump may reconstruct fields that were deleted)
        if chunk.choices and chunk.choices[0].delta:
            delta = chunk.choices[0].delta
            has_tool_calls = delta.tool_calls is not None and bool(delta.tool_calls)
            has_content = delta.content is not None
            chunks_received.append({"has_content": has_content, "has_tool_calls": has_tool_calls})

    processor = StreamingChunkAssembler(on_chunk_callback=track_chunks)
    await processor.process(simulate_stream(chunks), context=None)

    # During tool call phase, chunks should NOT have content field
    tool_call_chunks = [c for c in chunks_received if c["has_tool_calls"]]

    # All tool call chunks should have content stripped
    for i, chunk_info in enumerate(tool_call_chunks):
        assert not chunk_info["has_content"], (
            f"Chunk {i + 1}: Empty content should be stripped from tool call chunks, but has_content={chunk_info['has_content']}"
        )


@pytest.mark.asyncio
async def test_block_completion_ordering():
    """Test that blocks complete in correct order."""
    chunks = load_chunks("anthropic_multiple_tools_chunks.json")
    recorder = ChunkRecorder()
    processor = StreamingChunkAssembler(on_chunk_callback=recorder.on_chunk)

    await processor.process(simulate_stream(chunks), context=None)

    # Extract completion events in order
    completions = [
        (c["chunk_num"], c["just_completed_type"], c["just_completed_id"])
        for c in recorder.calls
        if c["just_completed_type"]
    ]

    # Should have: ContentStreamBlock, then 4 ToolCallStreamBlocks
    assert completions[0][1] == "ContentStreamBlock"
    assert all(c[1] == "ToolCallStreamBlock" for c in completions[1:])

    # Exactly 5 completions total
    assert len(completions) == 5


@pytest.mark.asyncio
async def test_current_block_tracking():
    """Test that current_block is correctly maintained."""
    chunks = load_chunks("anthropic_multiple_tools_chunks.json")
    recorder = ChunkRecorder()
    processor = StreamingChunkAssembler(on_chunk_callback=recorder.on_chunk)

    await processor.process(simulate_stream(chunks), context=None)

    # current_block should change as we transition between blocks
    block_types = [c["current_block_type"] for c in recorder.calls if c["current_block_type"]]

    # Should start with ContentStreamBlock
    assert block_types[0] == "ContentStreamBlock"

    # Then transition to ToolCallStreamBlock
    assert "ToolCallStreamBlock" in block_types

    # After finish_reason, we still have a current_block (the last one)
    final_call = recorder.calls[-1]
    assert final_call["current_block_type"] == "ToolCallStreamBlock"
