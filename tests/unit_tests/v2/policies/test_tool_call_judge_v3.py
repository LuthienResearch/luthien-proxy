# ABOUTME: Unit tests for V3 tool call judge policy
# ABOUTME: Tests EventBasedPolicy hook integration and clean judging logic

"""Tests for ToolCallJudgeV3Policy."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from litellm.types.utils import ModelResponse
from opentelemetry import trace

from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.policies.context import PolicyContext
from luthien_proxy.v2.policies.tool_call_judge_v3 import ToolCallJudgeV3Policy
from luthien_proxy.v2.policies.utils import JudgeResult
from luthien_proxy.v2.streaming.stream_blocks import ToolCallStreamBlock
from luthien_proxy.v2.streaming.utils import build_text_chunk


def create_tool_call_chunk(tool_index: int, tool_id: str, name: str = "", arguments: str = "") -> ModelResponse:
    """Helper to create tool call delta chunk."""
    delta_dict = {
        "tool_calls": [
            {
                "index": tool_index,
                "id": tool_id,
                "type": "function",
                "function": {},
            }
        ]
    }

    if name:
        delta_dict["tool_calls"][0]["function"]["name"] = name
    if arguments:
        delta_dict["tool_calls"][0]["function"]["arguments"] = arguments

    from litellm.types.utils import Delta, StreamingChoices

    return ModelResponse(
        id="test",
        choices=[StreamingChoices(index=0, delta=Delta(**delta_dict), finish_reason=None)],
        created=0,
        model="test-model",
        object="chat.completion.chunk",
    )


def create_finish_chunk(finish_reason: str = "tool_calls") -> ModelResponse:
    """Helper to create finish reason chunk."""
    from litellm.types.utils import Delta, StreamingChoices

    return ModelResponse(
        id="test",
        choices=[StreamingChoices(index=0, delta=Delta(), finish_reason=finish_reason)],
        created=0,
        model="test-model",
        object="chat.completion.chunk",
    )


class TestToolCallJudgeV3Policy:
    """Test V3 tool call judge policy implementation."""

    @pytest.fixture
    def policy(self):
        """Create policy with test configuration."""
        return ToolCallJudgeV3Policy(
            model="test-judge",
            api_base="http://test-judge:8080",
            api_key="test-key",
            probability_threshold=0.6,
        )

    @pytest.fixture
    def context(self):
        """Create test PolicyContext."""
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("test") as span:
            request = Request(model="gpt-4", messages=[{"role": "user", "content": "test"}])
            ctx = PolicyContext(
                call_id="test-call",
                span=span,
                request=request,
            )
            yield ctx

    @pytest.fixture
    def streaming_context(self, context):
        """Create test StreamingContext."""
        from luthien_proxy.v2.streaming.event_based_policy import StreamingContext

        outgoing = asyncio.Queue[ModelResponse]()
        return StreamingContext(
            policy_context=context,
            keepalive=None,
            outgoing=outgoing,
        )

    # ------------------------------------------------------------------
    # Initialization tests
    # ------------------------------------------------------------------

    def test_init_with_defaults(self):
        """Test policy initialization with default config."""
        policy = ToolCallJudgeV3Policy()
        assert policy._config.model == "openai/judge-scorer"
        assert policy._config.probability_threshold == 0.6

    def test_init_with_custom_config(self):
        """Test policy initialization with custom config."""
        policy = ToolCallJudgeV3Policy(
            model="custom-judge",
            api_base="http://custom:8080",
            probability_threshold=0.8,
        )
        assert policy._config.model == "custom-judge"
        assert policy._config.api_base == "http://custom:8080"
        assert policy._config.probability_threshold == 0.8

    def test_init_rejects_invalid_threshold(self):
        """Test that invalid threshold raises ValueError."""
        with pytest.raises(ValueError, match="probability_threshold must be between 0 and 1"):
            ToolCallJudgeV3Policy(probability_threshold=1.5)

    # ------------------------------------------------------------------
    # Non-streaming tests
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_on_request_passthrough(self, policy, context):
        """Test that requests pass through unchanged."""
        request = Request(model="gpt-4", messages=[{"role": "user", "content": "test"}])
        result = await policy.on_request(request, context)
        assert result == request

    @pytest.mark.asyncio
    async def test_on_response_no_tool_calls(self, policy, context):
        """Test response without tool calls passes through."""
        from litellm.types.utils import Choices, Message

        response = ModelResponse(
            id="test",
            choices=[
                Choices(
                    index=0,
                    message=Message(role="assistant", content="Hello!"),
                    finish_reason="stop",
                )
            ],
            created=0,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_response(response, context)
        assert result == response

    @pytest.mark.asyncio
    async def test_on_response_tool_call_passes_judge(self, policy, context):
        """Test response with tool call that passes judge."""
        from litellm.types.utils import ChatCompletionMessageToolCall, Choices, Function, Message

        response = ModelResponse(
            id="test",
            choices=[
                Choices(
                    index=0,
                    message=Message(
                        role="assistant",
                        content=None,
                        tool_calls=[
                            ChatCompletionMessageToolCall(
                                id="call_1",
                                type="function",
                                function=Function(name="get_weather", arguments='{"city": "NYC"}'),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            created=0,
            model="test-model",
            object="chat.completion",
        )

        # Mock judge to allow
        with patch.object(policy, "_call_judge", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeResult(
                probability=0.3,
                explanation="Safe",
                prompt=[],
                response_text="{}",
            )

            result = await policy.on_response(response, context)
            assert result == response
            mock_judge.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_response_tool_call_blocked(self, policy, context):
        """Test response with tool call that is blocked."""
        from litellm.types.utils import ChatCompletionMessageToolCall, Choices, Function, Message

        response = ModelResponse(
            id="test",
            choices=[
                Choices(
                    index=0,
                    message=Message(
                        role="assistant",
                        content=None,
                        tool_calls=[
                            ChatCompletionMessageToolCall(
                                id="call_1",
                                type="function",
                                function=Function(name="delete_database", arguments='{"confirm": true}'),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            created=0,
            model="test-model",
            object="chat.completion",
        )

        # Mock judge to block
        with patch.object(policy, "_call_judge", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeResult(
                probability=0.9,
                explanation="Destructive operation",
                prompt=[],
                response_text="{}",
            )

            result = await policy.on_response(response, context)
            assert result != response
            # Result should be blocked message
            assert "BLOCKED" in result.choices[0].message.content

    # ------------------------------------------------------------------
    # Streaming hook tests
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_on_stream_start_initializes_scratchpad(self, policy, context, streaming_context):
        """Test that on_stream_start initializes scratchpad metrics."""
        await policy.on_stream_start(context, streaming_context)

        assert context.scratchpad["tool_calls_judged"] == 0
        assert context.scratchpad["tool_calls_blocked"] == 0
        assert context.scratchpad["tool_calls_skipped"] == 0
        assert context.scratchpad["block_reason"] is None

    @pytest.mark.asyncio
    async def test_on_tool_call_delta_does_not_forward(self, policy, context, streaming_context):
        """Test that tool call deltas are not forwarded."""
        chunk = create_tool_call_chunk(0, "call_1", name="test_tool")
        block = ToolCallStreamBlock(id="call_1", index=0)

        # Call hook
        await policy.on_tool_call_delta(chunk, block, context, streaming_context)

        # Queue should be empty (nothing forwarded)
        assert streaming_context._outgoing.qsize() == 0

    @pytest.mark.asyncio
    async def test_on_tool_call_complete_passes_judge(self, policy, context, streaming_context):
        """Test tool call that passes judge is forwarded."""
        # Initialize scratchpad
        await policy.on_stream_start(context, streaming_context)

        # Create complete tool call block
        block = ToolCallStreamBlock(id="call_1", index=0)
        block.name = "get_weather"
        block.arguments = '{"city": "NYC"}'
        block.is_complete = True

        # Mock judge to allow
        with patch.object(policy, "_call_judge", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeResult(
                probability=0.3,
                explanation="Safe",
                prompt=[],
                response_text="{}",
            )

            await policy.on_tool_call_complete(block, context, streaming_context)

            # Check that tool call was judged
            assert context.scratchpad["tool_calls_judged"] == 1
            assert context.scratchpad["tool_calls_blocked"] == 0

            # Check that chunk was forwarded
            assert streaming_context._outgoing.qsize() == 1
            forwarded_chunk = await streaming_context._outgoing.get()
            assert forwarded_chunk.choices[0].delta.tool_calls is not None

    @pytest.mark.asyncio
    async def test_on_tool_call_complete_blocks_harmful_call(self, policy, context, streaming_context):
        """Test tool call that fails judge is blocked."""
        # Initialize scratchpad
        await policy.on_stream_start(context, streaming_context)

        # Create complete tool call block
        block = ToolCallStreamBlock(id="call_1", index=0)
        block.name = "delete_database"
        block.arguments = '{"confirm": true}'
        block.is_complete = True

        # Mock judge to block
        with patch.object(policy, "_call_judge", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeResult(
                probability=0.9,
                explanation="Destructive operation",
                prompt=[],
                response_text="{}",
            )

            await policy.on_tool_call_complete(block, context, streaming_context)

            # Check that tool call was blocked
            assert context.scratchpad["tool_calls_judged"] == 1
            assert context.scratchpad["tool_calls_blocked"] == 1
            assert streaming_context.is_output_finished()

            # Check that blocked message was sent
            assert streaming_context._outgoing.qsize() == 1
            blocked_chunk = await streaming_context._outgoing.get()
            content = blocked_chunk.choices[0].delta.content
            assert "BLOCKED" in content
            assert "delete_database" in content

    @pytest.mark.asyncio
    async def test_on_tool_call_complete_skips_after_output_finished(self, policy, context, streaming_context):
        """Test that tool calls are skipped after output is finished."""
        # Initialize scratchpad
        await policy.on_stream_start(context, streaming_context)

        # Mark output as finished
        streaming_context.mark_output_finished()

        # Create complete tool call block
        block = ToolCallStreamBlock(id="call_1", index=0)
        block.name = "test_tool"
        block.arguments = "{}"
        block.is_complete = True

        # Judge should NOT be called
        with patch.object(policy, "_call_judge", new_callable=AsyncMock) as mock_judge:
            await policy.on_tool_call_complete(block, context, streaming_context)

            # Judge should not have been called
            mock_judge.assert_not_called()

            # Should be marked as skipped
            assert context.scratchpad["tool_calls_skipped"] == 1
            assert context.scratchpad["tool_calls_judged"] == 0

            # No chunks should be sent
            assert streaming_context._outgoing.qsize() == 0

    @pytest.mark.asyncio
    async def test_on_stream_complete_emits_summary(self, policy, context):
        """Test that on_stream_complete emits summary with metrics."""
        # Set up scratchpad with metrics
        context.scratchpad["tool_calls_judged"] = 3
        context.scratchpad["tool_calls_blocked"] = 1
        context.scratchpad["tool_calls_skipped"] = 0
        context.scratchpad["block_reason"] = "harmful_tool: Dangerous"

        await policy.on_stream_complete(context)

        # Verify summary was emitted (check span events)
        # In real usage, this would be in the span events
        # For unit tests, we just verify it doesn't crash

    # ------------------------------------------------------------------
    # Full streaming flow tests
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_streaming_with_content_only(self, policy, context):
        """Test streaming with only content (no tool calls)."""
        incoming = asyncio.Queue[ModelResponse]()
        outgoing = asyncio.Queue[ModelResponse]()

        # Add content chunks
        await incoming.put(build_text_chunk("Hello", model="gpt-4"))
        await incoming.put(build_text_chunk(" world", model="gpt-4"))
        await incoming.put(create_finish_chunk("stop"))
        incoming.shutdown()

        # Process stream
        await policy.process_streaming_response(incoming, outgoing, context)

        # Content should be forwarded
        chunks = []
        try:
            while True:
                chunks.append(await asyncio.wait_for(outgoing.get(), timeout=0.1))
        except (asyncio.TimeoutError, asyncio.QueueShutDown):
            pass

        # Should have content chunks + finish chunk
        assert len(chunks) >= 2
        assert any("Hello" in str(c.choices[0].delta.content) for c in chunks if c.choices[0].delta.content)

    @pytest.mark.asyncio
    async def test_streaming_with_passed_tool_call(self, policy, context):
        """Test streaming with tool call that passes judge."""
        incoming = asyncio.Queue[ModelResponse]()
        outgoing = asyncio.Queue[ModelResponse]()

        # Add tool call chunks
        await incoming.put(create_tool_call_chunk(0, "call_1", name="get_weather"))
        await incoming.put(create_tool_call_chunk(0, "call_1", arguments='{"city":'))
        await incoming.put(create_tool_call_chunk(0, "call_1", arguments='"NYC"}'))
        await incoming.put(create_finish_chunk("tool_calls"))
        incoming.shutdown()

        # Mock judge to allow
        with patch.object(policy, "_call_judge", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeResult(
                probability=0.3,
                explanation="Safe",
                prompt=[],
                response_text="{}",
            )

            # Process stream
            await policy.process_streaming_response(incoming, outgoing, context)

            # Tool call should be forwarded as a single chunk
            chunks = []
            try:
                while True:
                    chunks.append(await asyncio.wait_for(outgoing.get(), timeout=0.1))
            except (asyncio.TimeoutError, asyncio.QueueShutDown):
                pass

            # Should have tool call chunk + finish chunk
            assert len(chunks) >= 1
            # Find tool call chunk
            tool_call_chunks = [c for c in chunks if c.choices[0].delta.tool_calls]
            assert len(tool_call_chunks) >= 1

    @pytest.mark.asyncio
    async def test_streaming_with_blocked_tool_call(self, policy, context):
        """Test streaming with tool call that is blocked."""
        incoming = asyncio.Queue[ModelResponse]()
        outgoing = asyncio.Queue[ModelResponse]()

        # Add tool call chunks
        await incoming.put(create_tool_call_chunk(0, "call_1", name="delete_database"))
        await incoming.put(create_tool_call_chunk(0, "call_1", arguments='{"confirm":'))
        await incoming.put(create_tool_call_chunk(0, "call_1", arguments="true}"))
        await incoming.put(create_finish_chunk("tool_calls"))
        incoming.shutdown()

        # Mock judge to block
        with patch.object(policy, "_call_judge", new_callable=AsyncMock) as mock_judge:
            mock_judge.return_value = JudgeResult(
                probability=0.9,
                explanation="Destructive",
                prompt=[],
                response_text="{}",
            )

            # Process stream
            await policy.process_streaming_response(incoming, outgoing, context)

            # Should have blocked message
            chunks = []
            try:
                while True:
                    chunks.append(await asyncio.wait_for(outgoing.get(), timeout=0.1))
            except (asyncio.TimeoutError, asyncio.QueueShutDown):
                pass

            # Should have exactly one chunk with blocked message
            assert len(chunks) >= 1
            first_chunk = chunks[0]
            content = first_chunk.choices[0].delta.content
            assert "BLOCKED" in content
            assert "delete_database" in content

    @pytest.mark.asyncio
    async def test_streaming_continues_after_blocking(self, policy, context):
        """Test that streaming continues processing after blocking for observability."""
        incoming = asyncio.Queue[ModelResponse]()
        outgoing = asyncio.Queue[ModelResponse]()

        # Add two tool calls - first will be blocked
        await incoming.put(create_tool_call_chunk(0, "call_1", name="harmful_tool"))
        await incoming.put(create_tool_call_chunk(0, "call_1", arguments="{}"))
        # Second tool call (should be skipped since output is finished)
        await incoming.put(create_tool_call_chunk(1, "call_2", name="safe_tool"))
        await incoming.put(create_tool_call_chunk(1, "call_2", arguments="{}"))
        await incoming.put(create_finish_chunk("tool_calls"))
        incoming.shutdown()

        # Mock judge to block first, allow second
        with patch.object(policy, "_call_judge", new_callable=AsyncMock) as mock_judge:
            mock_judge.side_effect = [
                JudgeResult(probability=0.9, explanation="Harmful", prompt=[], response_text="{}"),
                JudgeResult(probability=0.1, explanation="Safe", prompt=[], response_text="{}"),
            ]

            # Process stream
            await policy.process_streaming_response(incoming, outgoing, context)

            # First tool call should have been judged
            assert mock_judge.call_count == 1  # Second call was skipped (output finished)

            # Check scratchpad
            assert context.scratchpad["tool_calls_judged"] == 1
            assert context.scratchpad["tool_calls_blocked"] == 1
            assert context.scratchpad["tool_calls_skipped"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
