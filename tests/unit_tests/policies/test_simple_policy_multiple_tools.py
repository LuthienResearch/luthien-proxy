# ABOUTME: Test that validates SimplePolicy behavior with multiple tool calls
# ABOUTME: Regression test for duplicate response bug with finish_reason

"""Test SimplePolicy with multiple tool calls to validate finish_reason handling.

This test validates the hypothesis that SimplePolicy incorrectly sends multiple
chunks with finish_reason="tool_calls" when there are multiple tool calls,
causing duplicate response interpretation by clients like Claude Code.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from litellm.types.utils import Delta, ModelResponse

from luthien_proxy.llm.types import Request
from luthien_proxy.observability.transaction_recorder import TransactionRecorder
from luthien_proxy.policies import PolicyContext
from luthien_proxy.policies.noop_policy import NoOpPolicy
from luthien_proxy.policies.simple_policy import SimplePolicy
from luthien_proxy.streaming.policy_executor.executor import PolicyExecutor

FIXTURE_DIR = Path(__file__).parent.parent / "streaming" / "chunk_fixtures"


def load_chunks(filename: str) -> list[ModelResponse]:
    """Load streaming ModelResponse objects from JSON fixture file."""
    path = FIXTURE_DIR / filename
    with path.open() as f:
        chunk_dicts = json.load(f)

    responses = []
    for chunk_dict in chunk_dicts:
        if chunk_dict.get("choices"):
            for choice in chunk_dict["choices"]:
                if choice.get("delta") and isinstance(choice["delta"], dict):
                    choice["delta"] = Delta(**choice["delta"])

        mr = ModelResponse.model_validate(chunk_dict)

        if chunk_dict.get("choices"):
            original_finish = chunk_dict["choices"][0].get("finish_reason")
            if mr.choices and mr.choices[0].finish_reason != original_finish:
                mr.choices[0].finish_reason = original_finish

        responses.append(mr)

    return responses


async def simulate_stream(chunks: list[ModelResponse]):
    """Simulate async stream from ModelResponse list."""
    for mr in chunks:
        yield mr


class NoTransformPolicy(SimplePolicy):
    """SimplePolicy subclass that doesn't transform anything."""

    pass


def create_mock_policy_context() -> PolicyContext:
    """Create a mock PolicyContext for testing."""
    ctx = Mock(spec=PolicyContext)
    ctx.transaction_id = "test-transaction-id"
    ctx.request = Request(
        model="test-model",
        messages=[{"role": "user", "content": "test"}],
    )
    ctx.scratchpad = {}
    return ctx


def create_mock_recorder() -> TransactionRecorder:
    """Create a mock TransactionRecorder for testing."""
    recorder = Mock(spec=TransactionRecorder)
    recorder.add_ingress_chunk = Mock()
    recorder.add_egress_chunk = Mock()
    recorder.finalize_streaming_response = AsyncMock()
    return recorder


@pytest.mark.asyncio
async def test_simple_policy_multiple_tool_calls_finish_reason():
    """Test that SimplePolicy emits correct number of finish_reason chunks.

    Expected behavior: Only ONE chunk should have finish_reason="tool_calls".
    Bug behavior: Each tool call chunk has finish_reason="tool_calls".

    This test validates the hypothesis about the duplicate response bug.
    """
    # Load multiple tool call fixture (4 tool calls)
    chunks = load_chunks("anthropic_multiple_tools_chunks.json")

    # Setup
    policy = NoTransformPolicy()
    policy_ctx = create_mock_policy_context()
    recorder = create_mock_recorder()

    executor = PolicyExecutor(recorder=recorder, timeout_seconds=30.0)
    output_queue: asyncio.Queue[ModelResponse | None] = asyncio.Queue()

    # Run the executor
    await executor.process(
        input_stream=simulate_stream(chunks),
        output_queue=output_queue,
        policy=policy,
        policy_ctx=policy_ctx,
    )

    # Collect all output chunks
    output_chunks = []
    while True:
        chunk = await output_queue.get()
        if chunk is None:
            break
        output_chunks.append(chunk)

    # Count chunks with finish_reason
    chunks_with_finish_reason = [c for c in output_chunks if c.choices and c.choices[0].finish_reason]

    # Collect the finish reasons
    finish_reasons = [c.choices[0].finish_reason for c in chunks_with_finish_reason]

    print("\n=== Test Results ===")
    print(f"Total output chunks: {len(output_chunks)}")
    print(f"Chunks with finish_reason: {len(chunks_with_finish_reason)}")
    print(f"Finish reasons: {finish_reasons}")

    # Count tool_calls finish reasons specifically
    tool_calls_finish_reasons = [fr for fr in finish_reasons if fr == "tool_calls"]
    print(f"Chunks with finish_reason='tool_calls': {len(tool_calls_finish_reasons)}")

    # Exactly 1 chunk should have finish_reason="tool_calls".
    # Bug state (now fixed): each tool call had its own finish_reason, causing
    # clients to interpret each as a separate response.
    assert len(tool_calls_finish_reasons) == 1, (
        f"Expected exactly 1 chunk with finish_reason='tool_calls', "
        f"but got {len(tool_calls_finish_reasons)}. "
        f"This confirms the duplicate response bug."
    )


@pytest.mark.asyncio
async def test_noop_policy_multiple_tool_calls_finish_reason():
    """Test that NoOpPolicy emits correct number of finish_reason chunks.

    NoOpPolicy is the standard passthrough policy used in production.
    """
    # Load multiple tool call fixture (4 tool calls)
    chunks = load_chunks("anthropic_multiple_tools_chunks.json")

    # Setup with NoOpPolicy
    policy = NoOpPolicy()
    policy_ctx = create_mock_policy_context()
    recorder = create_mock_recorder()

    executor = PolicyExecutor(recorder=recorder, timeout_seconds=30.0)
    output_queue: asyncio.Queue[ModelResponse | None] = asyncio.Queue()

    # Run the executor
    await executor.process(
        input_stream=simulate_stream(chunks),
        output_queue=output_queue,
        policy=policy,
        policy_ctx=policy_ctx,
    )

    # Collect all output chunks
    output_chunks = []
    while True:
        chunk = await output_queue.get()
        if chunk is None:
            break
        output_chunks.append(chunk)

    # Count chunks with finish_reason
    chunks_with_finish_reason = [c for c in output_chunks if c.choices and c.choices[0].finish_reason]

    finish_reasons = [c.choices[0].finish_reason for c in chunks_with_finish_reason]
    tool_calls_finish_reasons = [fr for fr in finish_reasons if fr == "tool_calls"]

    print("\n=== NoOpPolicy Results ===")
    print(f"Total output chunks: {len(output_chunks)}")
    print(f"Chunks with finish_reason: {len(chunks_with_finish_reason)}")
    print(f"Finish reasons: {finish_reasons}")
    print(f"Chunks with finish_reason='tool_calls': {len(tool_calls_finish_reasons)}")

    # NoOpPolicy should have exactly 1 finish_reason (from the original stream)
    assert len(tool_calls_finish_reasons) == 1, (
        f"Expected exactly 1 chunk with finish_reason='tool_calls', but got {len(tool_calls_finish_reasons)}."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
