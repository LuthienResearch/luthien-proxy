# ABOUTME: Unit tests for EventDrivenToolCallJudgePolicy
# ABOUTME: Tests buffering, judging, blocking, and pass-through behavior

"""Tests for EventDrivenToolCallJudgePolicy.

Tests verify:
- Tool call buffering and aggregation
- Judge evaluation and blocking
- Pass-through for approved tool calls
- Content buffering and flushing
- Error handling
"""

from __future__ import annotations

import asyncio
from unittest.mock import Mock, patch

import pytest
from litellm.types.utils import Delta, ModelResponse, StreamingChoices

from luthien_proxy.v2.policies.context import PolicyContext
from luthien_proxy.v2.policies.event_driven_tool_call_judge import EventDrivenToolCallJudgePolicy


def create_test_context(call_id: str = "test") -> PolicyContext:
    """Create a PolicyContext for testing."""
    mock_span = Mock()
    return PolicyContext(call_id=call_id, span=mock_span)


def create_text_chunk(content: str) -> ModelResponse:
    """Create a streaming chunk with text content."""
    delta = Delta(content=content, role="assistant")
    choice = StreamingChoices(delta=delta, finish_reason=None, index=0)
    return ModelResponse(choices=[choice])


def create_tool_call_chunk(
    name_delta: str = "", args_delta: str = "", index: int = 0, tool_id: str = ""
) -> ModelResponse:
    """Create a streaming chunk with tool call delta."""
    delta = Delta(
        tool_calls=[
            {
                "index": index,
                "id": tool_id or f"call_{index}",
                "type": "function",
                "function": {"name": name_delta, "arguments": args_delta},
            }
        ],
        role=None,
    )
    choice = StreamingChoices(delta=delta, finish_reason=None, index=0)
    return ModelResponse(choices=[choice])


def create_finish_chunk(reason: str) -> ModelResponse:
    """Create a streaming chunk with finish_reason."""
    delta = Delta(role=None)
    choice = StreamingChoices(delta=delta, finish_reason=reason, index=0)
    return ModelResponse(choices=[choice])


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


def extract_content(chunks: list[ModelResponse]) -> str:
    """Extract text content from chunks (streaming or complete)."""
    parts = []
    for chunk in chunks:
        chunk_dict = chunk.model_dump() if hasattr(chunk, "model_dump") else dict(chunk)  # type: ignore
        choices = chunk_dict.get("choices", [])
        if choices:
            # Try streaming delta first
            delta = choices[0].get("delta", {})
            if isinstance(delta, dict) and delta.get("content"):
                parts.append(delta["content"])
            else:
                # Try complete message
                message = choices[0].get("message", {})
                if isinstance(message, dict) and message.get("content"):
                    parts.append(message["content"])
    return "".join(parts)


def mock_judge_response(probability: float, explanation: str = "Test explanation") -> Mock:
    """Create a mock judge response."""
    import json

    response_content = json.dumps({"probability": probability, "explanation": explanation})

    mock_message = Mock()
    mock_message.content = response_content

    mock_choice = Mock()
    mock_choice.message = mock_message

    mock_response = Mock()
    mock_response.choices = [mock_choice]

    return mock_response


@pytest.mark.asyncio
async def test_pass_through_content_only():
    """Test that content-only streams pass through without judging."""
    policy = EventDrivenToolCallJudgePolicy(probability_threshold=0.6)
    incoming = asyncio.Queue[ModelResponse]()
    outgoing = asyncio.Queue[ModelResponse]()
    context = create_test_context()

    # Add content chunks
    incoming.put_nowait(create_text_chunk("Hello "))
    incoming.put_nowait(create_text_chunk("world"))
    incoming.put_nowait(create_finish_chunk("stop"))
    incoming.shutdown()

    # Process
    await policy.process_streaming_response(incoming, outgoing, context)

    # Verify all chunks forwarded
    output = await drain_queue(outgoing)
    content = extract_content(output)
    assert content == "Hello world"

    # Verify finish chunk forwarded
    finish_chunks = [c for c in output if c.choices[0].finish_reason == "stop"]  # type: ignore
    assert len(finish_chunks) == 1


@pytest.mark.asyncio
@patch("luthien_proxy.v2.policies.event_driven_tool_call_judge.acompletion")
async def test_tool_call_approved(mock_acompletion):
    """Test that approved tool calls are forwarded."""
    # Mock judge to approve (low probability)
    mock_acompletion.return_value = mock_judge_response(probability=0.2)

    policy = EventDrivenToolCallJudgePolicy(probability_threshold=0.6)
    incoming = asyncio.Queue[ModelResponse]()
    outgoing = asyncio.Queue[ModelResponse]()
    context = create_test_context()

    # Add complete tool call in single chunk
    incoming.put_nowait(
        create_tool_call_chunk(name_delta="get_weather", args_delta='{"location":"NYC"}', index=0, tool_id="call_1")
    )
    incoming.put_nowait(create_finish_chunk("tool_calls"))
    incoming.shutdown()

    # Process
    await policy.process_streaming_response(incoming, outgoing, context)

    # Verify judge was called
    assert mock_acompletion.called

    # Verify tool call chunks were forwarded
    output = await drain_queue(outgoing)
    assert len(output) > 0

    # Verify no blocked message
    content = extract_content(output)
    assert "BLOCKED" not in content


@pytest.mark.asyncio
@patch("luthien_proxy.v2.policies.event_driven_tool_call_judge.acompletion")
async def test_tool_call_blocked(mock_acompletion):
    """Test that blocked tool calls send replacement message."""
    # Mock judge to block (high probability)
    mock_acompletion.return_value = mock_judge_response(probability=0.9, explanation="Risky operation")

    policy = EventDrivenToolCallJudgePolicy(probability_threshold=0.6)
    incoming = asyncio.Queue[ModelResponse]()
    outgoing = asyncio.Queue[ModelResponse]()
    context = create_test_context()

    # Add complete tool call in single chunk
    incoming.put_nowait(
        create_tool_call_chunk(name_delta="delete_file", args_delta='{"path":"/etc/passwd"}', index=0, tool_id="call_1")
    )
    incoming.put_nowait(create_finish_chunk("tool_calls"))
    incoming.shutdown()

    # Process
    await policy.process_streaming_response(incoming, outgoing, context)

    # Verify judge was called
    assert mock_acompletion.called

    # Verify blocked message sent
    output = await drain_queue(outgoing)
    content = extract_content(output)
    assert "BLOCKED" in content
    assert "delete_file" in content
    assert "0.9" in content or "0.90" in content  # Probability in message


@pytest.mark.asyncio
@patch("luthien_proxy.v2.policies.event_driven_tool_call_judge.acompletion")
async def test_content_then_approved_tool_call(mock_acompletion):
    """Test content followed by approved tool call."""
    # Mock judge to approve
    mock_acompletion.return_value = mock_judge_response(probability=0.1)

    policy = EventDrivenToolCallJudgePolicy(probability_threshold=0.6)
    incoming = asyncio.Queue[ModelResponse]()
    outgoing = asyncio.Queue[ModelResponse]()
    context = create_test_context()

    # Add content, then tool call
    incoming.put_nowait(create_text_chunk("Let me check the weather. "))
    incoming.put_nowait(create_tool_call_chunk(name_delta="get_weather", args_delta='{"city":"NYC"}', index=0))
    incoming.put_nowait(create_finish_chunk("tool_calls"))
    incoming.shutdown()

    # Process
    await policy.process_streaming_response(incoming, outgoing, context)

    # Verify both content and tool call forwarded
    output = await drain_queue(outgoing)
    content = extract_content(output)
    assert "Let me check the weather." in content
    assert "BLOCKED" not in content


@pytest.mark.asyncio
@patch("luthien_proxy.v2.policies.event_driven_tool_call_judge.acompletion")
async def test_content_then_blocked_tool_call(mock_acompletion):
    """Test content followed by blocked tool call."""
    # Mock judge to block
    mock_acompletion.return_value = mock_judge_response(probability=0.9)

    policy = EventDrivenToolCallJudgePolicy(probability_threshold=0.6)
    incoming = asyncio.Queue[ModelResponse]()
    outgoing = asyncio.Queue[ModelResponse]()
    context = create_test_context()

    # Add content, then tool call
    incoming.put_nowait(create_text_chunk("Let me delete that file. "))
    incoming.put_nowait(create_tool_call_chunk(name_delta="delete_file", args_delta='{"path":"/data"}', index=0))
    incoming.put_nowait(create_finish_chunk("tool_calls"))
    incoming.shutdown()

    # Process
    await policy.process_streaming_response(incoming, outgoing, context)

    # Verify content was NOT forwarded (entire buffer blocked)
    output = await drain_queue(outgoing)
    content = extract_content(output)
    assert "BLOCKED" in content
    assert "delete_file" in content


@pytest.mark.asyncio
@patch("luthien_proxy.v2.policies.event_driven_tool_call_judge.acompletion")
async def test_multiple_tool_calls_all_approved(mock_acompletion):
    """Test multiple tool calls that are all approved."""
    # Mock judge to approve all
    mock_acompletion.return_value = mock_judge_response(probability=0.1)

    policy = EventDrivenToolCallJudgePolicy(probability_threshold=0.6)
    incoming = asyncio.Queue[ModelResponse]()
    outgoing = asyncio.Queue[ModelResponse]()
    context = create_test_context()

    # Add two tool calls
    incoming.put_nowait(create_tool_call_chunk(name_delta="get_weather", args_delta='{"city":"NYC"}', index=0))
    incoming.put_nowait(create_tool_call_chunk(name_delta="get_time", args_delta='{"tz":"EST"}', index=1))
    incoming.put_nowait(create_finish_chunk("tool_calls"))
    incoming.shutdown()

    # Process
    await policy.process_streaming_response(incoming, outgoing, context)

    # Verify both tool calls were judged
    assert mock_acompletion.call_count == 2

    # Verify all forwarded
    output = await drain_queue(outgoing)
    assert len(output) > 0
    content = extract_content(output)
    assert "BLOCKED" not in content


@pytest.mark.asyncio
@patch("luthien_proxy.v2.policies.event_driven_tool_call_judge.acompletion")
async def test_multiple_tool_calls_one_blocked(mock_acompletion):
    """Test multiple tool calls where second one is blocked."""

    # Mock judge to approve first, block second
    def judge_side_effect(*args, **kwargs):
        messages = kwargs.get("messages", [])
        user_content = messages[1]["content"] if len(messages) > 1 else ""

        if "get_weather" in user_content:
            return mock_judge_response(probability=0.1)
        else:
            return mock_judge_response(probability=0.9)

    mock_acompletion.side_effect = judge_side_effect

    policy = EventDrivenToolCallJudgePolicy(probability_threshold=0.6)
    incoming = asyncio.Queue[ModelResponse]()
    outgoing = asyncio.Queue[ModelResponse]()
    context = create_test_context()

    # Add two tool calls
    incoming.put_nowait(create_tool_call_chunk(name_delta="get_weather", args_delta='{"city":"NYC"}', index=0))
    incoming.put_nowait(create_tool_call_chunk(name_delta="delete_file", args_delta='{"path":"/data"}', index=1))
    incoming.put_nowait(create_finish_chunk("tool_calls"))
    incoming.shutdown()

    # Process
    await policy.process_streaming_response(incoming, outgoing, context)

    # Verify both were judged (up to the blocking one)
    assert mock_acompletion.call_count >= 1

    # Verify blocked message sent
    output = await drain_queue(outgoing)
    content = extract_content(output)
    assert "BLOCKED" in content


@pytest.mark.asyncio
async def test_empty_stream():
    """Test empty stream handling."""
    policy = EventDrivenToolCallJudgePolicy(probability_threshold=0.6)
    incoming = asyncio.Queue[ModelResponse]()
    outgoing = asyncio.Queue[ModelResponse]()
    context = create_test_context()

    # Empty stream
    incoming.shutdown()

    # Process
    await policy.process_streaming_response(incoming, outgoing, context)

    # Verify no output
    output = await drain_queue(outgoing)
    assert len(output) == 0


@pytest.mark.asyncio
@patch("luthien_proxy.v2.policies.event_driven_tool_call_judge.acompletion")
async def test_judge_api_error(mock_acompletion):
    """Test handling of judge API errors."""
    # Mock judge to raise error
    mock_acompletion.side_effect = Exception("API error")

    policy = EventDrivenToolCallJudgePolicy(probability_threshold=0.6)
    incoming = asyncio.Queue[ModelResponse]()
    outgoing = asyncio.Queue[ModelResponse]()
    context = create_test_context()

    # Add tool call
    incoming.put_nowait(create_tool_call_chunk(name_delta="get_weather", args_delta='{"city":"NYC"}', index=0))
    incoming.put_nowait(create_finish_chunk("tool_calls"))
    incoming.shutdown()

    # Process - should raise
    with pytest.raises(Exception, match="API error"):
        await policy.process_streaming_response(incoming, outgoing, context)


@pytest.mark.asyncio
async def test_threshold_validation():
    """Test that threshold is validated."""
    with pytest.raises(ValueError, match="probability_threshold must be between 0 and 1"):
        EventDrivenToolCallJudgePolicy(probability_threshold=1.5)

    with pytest.raises(ValueError, match="probability_threshold must be between 0 and 1"):
        EventDrivenToolCallJudgePolicy(probability_threshold=-0.1)


@pytest.mark.asyncio
@patch("luthien_proxy.v2.policies.event_driven_tool_call_judge.acompletion")
async def test_keepalive_called(mock_acompletion):
    """Test that keepalive is called during judge evaluation."""
    # Mock judge
    mock_acompletion.return_value = mock_judge_response(probability=0.1)

    # Create mock keepalive
    mock_keepalive = Mock()

    policy = EventDrivenToolCallJudgePolicy(probability_threshold=0.6)
    incoming = asyncio.Queue[ModelResponse]()
    outgoing = asyncio.Queue[ModelResponse]()
    context = create_test_context()

    # Add tool call
    incoming.put_nowait(create_tool_call_chunk(name_delta="get_weather", args_delta='{"city":"NYC"}', index=0))
    incoming.put_nowait(create_finish_chunk("tool_calls"))
    incoming.shutdown()

    # Process with keepalive
    await policy.process_streaming_response(incoming, outgoing, context, keepalive=mock_keepalive)

    # Verify keepalive was called
    assert mock_keepalive.called
