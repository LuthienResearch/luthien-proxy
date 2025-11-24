"""Unit tests for ToolCallJudgePolicy.

Tests the policy's behavior including:
- Streaming chunk forwarding and buffering
- Tool call blocking/allowing based on judge evaluation
- Non-streaming response handling
- Error handling and fail-secure behavior
- Stream completion and finish_reason handling
- Configuration and initialization

Note: Utility function tests are in test_tool_call_judge_utils.py
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from litellm.types.utils import (
    ChatCompletionDeltaToolCall,
    Delta,
    Function,
    ModelResponse,
    StreamingChoices,
)

from luthien_proxy.messages import Request
from luthien_proxy.policies import PolicyContext
from luthien_proxy.policies.tool_call_judge_policy import ToolCallJudgePolicy
from luthien_proxy.policy_core.chunk_builders import create_text_chunk
from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext
from luthien_proxy.streaming.stream_blocks import ToolCallStreamBlock
from luthien_proxy.streaming.stream_state import StreamState


def create_mock_context(
    transaction_id: str = "test-call-id",
    just_completed=None,
    raw_chunks: list[ModelResponse] | None = None,
) -> StreamingPolicyContext:
    """Create a mock StreamingPolicyContext for testing."""
    ctx = Mock(spec=StreamingPolicyContext)

    # Create PolicyContext
    ctx.policy_ctx = Mock(spec=PolicyContext)
    ctx.policy_ctx.transaction_id = transaction_id
    ctx.policy_ctx.request = Request(
        model="test-model",
        messages=[{"role": "user", "content": "test"}],
    )
    ctx.policy_ctx.scratchpad = {}

    # Create stream state
    ctx.original_streaming_response_state = StreamState()
    ctx.original_streaming_response_state.just_completed = just_completed
    ctx.original_streaming_response_state.raw_chunks = raw_chunks or []

    # Egress queue and observability
    # Use Mock (not AsyncMock) because put_nowait is sync, but make put async
    ctx.egress_queue = Mock()
    ctx.egress_queue.put_nowait = Mock()
    ctx.egress_queue.put = AsyncMock()
    ctx.observability = Mock()
    ctx.observability.emit_event_nonblocking = Mock()

    return ctx


class TestToolCallJudgePolicyContentForwarding:
    """Test that ToolCallJudgePolicy forwards content chunks (Bug #1 regression test)."""

    @pytest.mark.asyncio
    async def test_on_content_delta_forwards_chunks(self):
        """REGRESSION TEST: Verify content chunks are forwarded to egress.

        This test would have caught the bug where ToolCallJudgePolicy didn't
        implement on_content_delta, causing no chunks to reach the client.
        """
        policy = ToolCallJudgePolicy()

        # Create a content chunk
        content_chunk = ModelResponse(
            id="test",
            object="chat.completion.chunk",
            created=123,
            model="test",
            choices=[{"index": 0, "delta": {"content": "hello world"}, "finish_reason": None}],
        )

        ctx = create_mock_context(raw_chunks=[content_chunk])

        # Call on_content_delta
        await policy.on_content_delta(ctx)

        # CRITICAL: Verify chunk was forwarded to egress
        ctx.egress_queue.put_nowait.assert_called_once_with(content_chunk)

    @pytest.mark.asyncio
    async def test_on_content_delta_handles_empty_chunks(self):
        """Test that on_content_delta handles edge case of no chunks."""
        policy = ToolCallJudgePolicy()
        ctx = create_mock_context(raw_chunks=[])

        # Should not raise, should not emit
        await policy.on_content_delta(ctx)
        ctx.egress_queue.put_nowait.assert_not_called()


class TestCreateTextChunkDeltaType:
    """Test that create_text_chunk uses proper Delta objects (Bug #2 regression test)."""

    def test_create_text_chunk_uses_delta_object(self):
        """REGRESSION TEST: Verify create_text_chunk creates Delta objects, not dicts.

        This test would have caught the bug where create_text_chunk used
        delta={"content": text} instead of delta=Delta(content=text),
        breaking the Anthropic SSE assembler.
        """
        chunk = create_text_chunk("test content")

        # CRITICAL: Delta must be a proper Delta object, not a dict
        delta = chunk.choices[0].delta
        assert isinstance(delta, Delta), f"Expected Delta object, got {type(delta)}"
        assert delta.content == "test content"

    def test_create_text_chunk_with_finish_reason(self):
        """Test that finish_reason is properly set."""
        chunk = create_text_chunk("test", finish_reason="stop")

        assert isinstance(chunk.choices[0].delta, Delta)
        assert chunk.choices[0].finish_reason == "stop"

    def test_create_text_chunk_with_empty_string(self):
        """Test that empty string creates valid Delta."""
        chunk = create_text_chunk("")

        delta = chunk.choices[0].delta
        assert isinstance(delta, Delta)


class TestToolCallJudgePolicyBlockedMessageChunks:
    """Test that blocked messages send separate content + finish chunks (Bug #3 regression test)."""

    @pytest.mark.asyncio
    async def test_blocked_tool_call_sends_two_chunks(self):
        """REGRESSION TEST: Verify blocked messages send content chunk + finish chunk separately.

        This test would have caught the bug where we sent content and finish_reason
        in a single chunk, causing the Anthropic SSE assembler to only process
        the content and miss the finish_reason, resulting in missing
        content_block_stop and message_delta events.
        """
        policy = ToolCallJudgePolicy(probability_threshold=0.0)  # Block everything

        ctx = create_mock_context(transaction_id="test-call")

        # First, buffer the tool call using proper Delta object
        tc = ChatCompletionDeltaToolCall(
            id="call-123",
            type="function",
            index=0,
            function=Function(name="dangerous_action", arguments='{"confirm": true}'),
        )
        tool_call_chunk = ModelResponse(
            id="test",
            object="chat.completion.chunk",
            created=123,
            model="test",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(tool_calls=[tc]),
                    finish_reason=None,
                )
            ],
        )
        ctx.original_streaming_response_state.raw_chunks = [tool_call_chunk]
        await policy.on_tool_call_delta(ctx)

        # Now create a completed tool call block
        block = ToolCallStreamBlock(
            id="call-123",
            index=0,
            name="dangerous_action",
            arguments='{"confirm": true}',
        )
        block.is_complete = True
        ctx.original_streaming_response_state.just_completed = block

        # Mock the judge to always return high probability (block)
        async def mock_call_judge(name, arguments, config, judge_instructions):
            from luthien_proxy.policies.tool_call_judge_utils import JudgeResult

            return JudgeResult(
                probability=1.0,  # High probability = block
                explanation="dangerous action",
                prompt=[],
                response_text="",
            )

        with patch("luthien_proxy.policies.tool_call_judge_utils.call_judge", side_effect=mock_call_judge):
            await policy.on_tool_call_complete(ctx)

        # CRITICAL: Should have called egress_queue.put TWICE
        # Once for content chunk, once for finish chunk
        assert ctx.egress_queue.put.call_count == 2, "Blocked message must send 2 chunks: content + finish"

        # Verify first chunk has content, no finish_reason (streaming chunk)
        first_call = ctx.egress_queue.put.call_args_list[0]
        first_chunk = first_call[0][0]
        assert isinstance(first_chunk.choices[0].delta, Delta), "First chunk should have Delta object"
        assert first_chunk.choices[0].delta.content is not None, "First chunk must have content"
        assert first_chunk.choices[0].finish_reason is None, "First chunk should not have finish_reason"

        # Verify second chunk has finish_reason, no/empty content (streaming chunk)
        second_call = ctx.egress_queue.put.call_args_list[1]
        second_chunk = second_call[0][0]
        assert isinstance(second_chunk.choices[0].delta, Delta), "Second chunk should have Delta object"
        assert second_chunk.choices[0].finish_reason == "stop", "Second chunk must have finish_reason=stop"
        # Content should be None or empty in second chunk
        assert not second_chunk.choices[0].delta.content, "Second chunk should have empty/None content"

    @pytest.mark.asyncio
    async def test_allowed_tool_call_cleans_up_buffer(self):
        """Test that allowed tool calls clean up buffered data."""
        policy = ToolCallJudgePolicy(probability_threshold=1.0)  # Allow everything

        # Buffer a tool call first using proper Delta object
        tc = ChatCompletionDeltaToolCall(
            id="call-123",
            type="function",
            index=0,
            function=Function(name="safe_action", arguments='{"ok": true}'),
        )
        tool_call_chunk = ModelResponse(
            id="test",
            object="chat.completion.chunk",
            created=123,
            model="test",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(tool_calls=[tc]),
                    finish_reason=None,
                )
            ],
        )

        ctx = create_mock_context(
            transaction_id="test-call",
            raw_chunks=[tool_call_chunk],
        )

        # Buffer the tool call
        await policy.on_tool_call_delta(ctx)

        # Verify it was buffered
        assert ("test-call", 0) in policy._buffered_tool_calls

        # Now complete it (should be allowed)
        block = ToolCallStreamBlock(
            id="call-123",
            index=0,
            name="safe_action",
            arguments='{"ok": true}',
        )
        block.is_complete = True
        ctx.original_streaming_response_state.just_completed = block

        # Mock judge to allow
        async def mock_evaluate(tool_call, obs_ctx):
            return None  # None = allowed

        with patch.object(policy, "_evaluate_and_maybe_block", side_effect=mock_evaluate):
            await policy.on_tool_call_complete(ctx)

        # Verify buffer was cleaned up
        assert ("test-call", 0) not in policy._buffered_tool_calls


class TestToolCallJudgePolicyToolCallBuffering:
    """Test that tool call deltas are buffered correctly."""

    @pytest.mark.asyncio
    async def test_on_tool_call_delta_buffers_data(self):
        """Test that tool call deltas are accumulated in buffer."""
        policy = ToolCallJudgePolicy()

        # Create chunks with tool call parts using proper Delta objects
        # Chunk 1: id and name
        tc1 = ChatCompletionDeltaToolCall(
            id="call-123",
            type="function",
            index=0,
            function=Function(name="test_tool", arguments=None),
        )
        chunk1 = ModelResponse(
            id="test",
            object="chat.completion.chunk",
            created=123,
            model="test",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(tool_calls=[tc1]),
                    finish_reason=None,
                )
            ],
        )

        # Chunk 2: first part of arguments
        tc2 = ChatCompletionDeltaToolCall(
            index=0,
            function=Function(name=None, arguments='{"key":'),
        )
        chunk2 = ModelResponse(
            id="test",
            object="chat.completion.chunk",
            created=123,
            model="test",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(tool_calls=[tc2]),
                    finish_reason=None,
                )
            ],
        )

        # Chunk 3: rest of arguments
        tc3 = ChatCompletionDeltaToolCall(
            index=0,
            function=Function(name=None, arguments='"value"}'),
        )
        chunk3 = ModelResponse(
            id="test",
            object="chat.completion.chunk",
            created=123,
            model="test",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(tool_calls=[tc3]),
                    finish_reason=None,
                )
            ],
        )

        ctx = create_mock_context(transaction_id="test-call")

        # Process chunks
        for chunk in [chunk1, chunk2, chunk3]:
            ctx.original_streaming_response_state.raw_chunks = [chunk]
            await policy.on_tool_call_delta(ctx)

        # Verify buffered data
        key = ("test-call", 0)
        assert key in policy._buffered_tool_calls
        buffered = policy._buffered_tool_calls[key]

        assert buffered["id"] == "call-123"
        assert buffered["name"] == "test_tool"
        assert buffered["arguments"] == '{"key":"value"}'


class TestToolCallJudgePolicyNonStreaming:
    """Test non-streaming response handling."""

    @pytest.mark.asyncio
    async def test_on_response_with_no_tool_calls(self):
        """Test that on_response passes through responses with no tool calls."""
        from luthien_proxy.policies import PolicyContext

        policy = ToolCallJudgePolicy()

        # Create response with just text content
        response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello world"},
                    "finish_reason": "stop",
                }
            ],
        )

        ctx = PolicyContext(
            transaction_id="test",
            request=Request(model="test", messages=[{"role": "user", "content": "hi"}]),
            observability=Mock(),
        )

        result = await policy.on_response(response, ctx)

        # Should pass through unchanged
        assert result is response

    @pytest.mark.asyncio
    async def test_on_response_blocks_harmful_tool_call(self):
        """Test that on_response blocks tool calls judged as harmful."""
        from litellm.types.utils import ChatCompletionMessageToolCall

        from luthien_proxy.policies import PolicyContext

        policy = ToolCallJudgePolicy(probability_threshold=0.5)

        # Create response with tool call
        response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            ChatCompletionMessageToolCall(
                                id="call-123",
                                function=Function(name="rm_rf", arguments='{"path": "/"}'),
                            )
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        )

        ctx = PolicyContext(
            transaction_id="test",
            request=Request(model="test", messages=[{"role": "user", "content": "hi"}]),
            observability=Mock(),
        )
        ctx.observability.emit_event_nonblocking = Mock()

        # Mock judge to block
        async def mock_call_judge(name, arguments, config, judge_instructions):
            from luthien_proxy.policies.tool_call_judge_utils import JudgeResult

            return JudgeResult(
                probability=0.9,  # High probability = block
                explanation="dangerous operation",
                prompt=[],
                response_text="",
            )

        with patch("luthien_proxy.policies.tool_call_judge_policy.call_judge", side_effect=mock_call_judge):
            result = await policy.on_response(response, ctx)

        # Should return a blocked response, not the original
        assert result is not response
        assert result.choices[0].message.content is not None
        assert "BLOCKED" in result.choices[0].message.content

    @pytest.mark.asyncio
    async def test_on_response_allows_safe_tool_call(self):
        """Test that on_response allows tool calls judged as safe."""
        from litellm.types.utils import ChatCompletionMessageToolCall

        from luthien_proxy.policies import PolicyContext

        policy = ToolCallJudgePolicy(probability_threshold=0.5)

        # Create response with tool call
        response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            ChatCompletionMessageToolCall(
                                id="call-123",
                                function=Function(name="get_weather", arguments='{"city": "SF"}'),
                            )
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        )

        ctx = PolicyContext(
            transaction_id="test",
            request=Request(model="test", messages=[{"role": "user", "content": "hi"}]),
            observability=Mock(),
        )
        ctx.observability.emit_event_nonblocking = Mock()

        # Mock judge to allow
        async def mock_call_judge(name, arguments, config, judge_instructions):
            from luthien_proxy.policies.tool_call_judge_utils import JudgeResult

            return JudgeResult(
                probability=0.2,  # Low probability = allow
                explanation="safe operation",
                prompt=[],
                response_text="",
            )

        with patch("luthien_proxy.policies.tool_call_judge_policy.call_judge", side_effect=mock_call_judge):
            result = await policy.on_response(response, ctx)

        # Should pass through unchanged
        assert result is response


class TestToolCallJudgeErrorHandling:
    """Test error handling in tool call judging."""

    @pytest.mark.asyncio
    async def test_judge_failure_blocks_tool_call(self):
        """Test that judge failures result in blocking (fail-secure)."""
        from luthien_proxy.policies import PolicyContext

        policy = ToolCallJudgePolicy()

        # Create response with tool call
        response = ModelResponse(
            id="test",
            object="chat.completion",
            created=123,
            model="test",
            choices=[
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-123",
                                "type": "function",
                                "function": {
                                    "name": "test_tool",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        )

        ctx = PolicyContext(
            transaction_id="test",
            request=Request(model="test", messages=[{"role": "user", "content": "hi"}]),
            observability=Mock(),
        )
        ctx.observability.emit_event_nonblocking = Mock()

        # Mock judge to raise an exception
        async def mock_call_judge(*args, **kwargs):
            raise Exception("Judge service unavailable")

        with patch("luthien_proxy.policies.tool_call_judge_policy.call_judge", side_effect=mock_call_judge):
            result = await policy.on_response(response, ctx)

        # Should block (fail-secure)
        assert result is not response
        assert "SECURITY BLOCK" in result.choices[0].message.content

    @pytest.mark.asyncio
    async def test_streaming_judge_failure_blocks_tool_call(self):
        """Test that judge failures in streaming also block (fail-secure)."""
        policy = ToolCallJudgePolicy()

        # Buffer a tool call first
        tc = ChatCompletionDeltaToolCall(
            id="call-123",
            type="function",
            index=0,
            function=Function(name="test_tool", arguments="{}"),
        )
        chunk = ModelResponse(
            id="test",
            object="chat.completion.chunk",
            created=123,
            model="test",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(tool_calls=[tc]),
                    finish_reason=None,
                )
            ],
        )

        ctx = create_mock_context(transaction_id="test-call", raw_chunks=[chunk])
        await policy.on_tool_call_delta(ctx)

        # Create completed block
        block = ToolCallStreamBlock(
            id="call-123",
            index=0,
            name="test_tool",
            arguments="{}",
        )
        block.is_complete = True
        ctx.original_streaming_response_state.just_completed = block

        # Mock judge to fail
        async def mock_evaluate(*args, **kwargs):
            raise Exception("Judge failure")

        with patch("luthien_proxy.policies.tool_call_judge_utils.call_judge", side_effect=mock_evaluate):
            await policy.on_tool_call_complete(ctx)

        # Should have blocked by sending content + finish chunks
        assert ctx.egress_queue.put.call_count >= 1
        first_call = ctx.egress_queue.put.call_args_list[0]
        first_chunk = first_call[0][0]
        assert "SECURITY BLOCK" in str(first_chunk.choices[0].delta.content)


class TestToolCallJudgeStreamComplete:
    """Test on_stream_complete hook."""

    @pytest.mark.asyncio
    async def test_on_stream_complete_emits_finish_for_tool_calls(self):
        """Test that on_stream_complete emits finish_reason for tool call responses."""
        from luthien_proxy.streaming.stream_blocks import ToolCallStreamBlock

        policy = ToolCallJudgePolicy()

        # Create context with tool call blocks
        ctx = create_mock_context()
        ctx.original_streaming_response_state.finish_reason = "tool_calls"
        ctx.original_streaming_response_state.blocks = [
            ToolCallStreamBlock(id="call-1", index=0, name="test", arguments="{}")
        ]
        ctx.original_streaming_response_state.raw_chunks = [
            ModelResponse(id="chunk-1", object="chat.completion.chunk", created=123, model="test-model", choices=[])
        ]

        await policy.on_stream_complete(ctx)

        # Should have emitted finish chunk
        ctx.egress_queue.put.assert_called_once()
        finish_chunk = ctx.egress_queue.put.call_args[0][0]
        assert finish_chunk.choices[0].finish_reason == "tool_calls"

    @pytest.mark.asyncio
    async def test_on_stream_complete_skips_if_no_finish_reason(self):
        """Test that on_stream_complete does nothing if no finish_reason."""
        policy = ToolCallJudgePolicy()

        ctx = create_mock_context()
        ctx.original_streaming_response_state.finish_reason = None

        await policy.on_stream_complete(ctx)

        # Should not emit anything
        ctx.egress_queue.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_stream_complete_skips_if_blocked(self):
        """Test that on_stream_complete skips emitting finish if call was blocked."""
        from luthien_proxy.streaming.stream_blocks import ToolCallStreamBlock

        policy = ToolCallJudgePolicy()

        # Mark this call as blocked
        ctx = create_mock_context(transaction_id="blocked-call")
        policy._blocked_calls.add("blocked-call")

        ctx.original_streaming_response_state.finish_reason = "tool_calls"
        ctx.original_streaming_response_state.blocks = [
            ToolCallStreamBlock(id="call-1", index=0, name="test", arguments="{}")
        ]

        await policy.on_stream_complete(ctx)

        # Should not emit finish chunk (already sent stop in blocking logic)
        ctx.egress_queue.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_stream_complete_skips_for_content_only(self):
        """Test that on_stream_complete doesn't emit for content-only responses."""
        from luthien_proxy.streaming.stream_blocks import ContentStreamBlock

        policy = ToolCallJudgePolicy()

        ctx = create_mock_context()
        ctx.original_streaming_response_state.finish_reason = "stop"
        # Only content blocks, no tool calls
        content_block = ContentStreamBlock(id="content")
        content_block.content = "hello world"
        ctx.original_streaming_response_state.blocks = [content_block]

        await policy.on_stream_complete(ctx)

        # Should not emit anything (content responses handle finish_reason themselves)
        ctx.egress_queue.put.assert_not_called()


class TestToolCallJudgePolicyConfiguration:
    """Test policy configuration and initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default configuration."""
        policy = ToolCallJudgePolicy()

        assert policy._config.model == "openai/gpt-4"
        assert policy._config.probability_threshold == 0.6
        assert policy._config.temperature == 0.0
        assert policy._config.max_tokens == 256

    def test_init_with_custom_config(self):
        """Test initialization with custom configuration."""
        policy = ToolCallJudgePolicy(
            model="claude-3-5-sonnet-20241022",
            api_base="http://custom:8000",
            api_key="test-key",
            probability_threshold=0.8,
            temperature=0.5,
            max_tokens=512,
            judge_instructions="Custom instructions",
            blocked_message_template="Custom template: {tool_name}",
        )

        assert policy._config.model == "claude-3-5-sonnet-20241022"
        assert policy._config.api_base == "http://custom:8000"
        assert policy._config.api_key == "test-key"
        assert policy._config.probability_threshold == 0.8
        assert policy._config.temperature == 0.5
        assert policy._config.max_tokens == 512
        assert policy._judge_instructions == "Custom instructions"
        assert "Custom template" in policy._blocked_message_template

    def test_init_invalid_threshold_raises(self):
        """Test that invalid probability threshold raises ValueError."""
        with pytest.raises(ValueError, match="probability_threshold must be between 0 and 1"):
            ToolCallJudgePolicy(probability_threshold=1.5)

        with pytest.raises(ValueError, match="probability_threshold must be between 0 and 1"):
            ToolCallJudgePolicy(probability_threshold=-0.1)

    def test_short_policy_name(self):
        """Test that short_policy_name returns expected value."""
        policy = ToolCallJudgePolicy()
        assert policy.short_policy_name == "ToolJudge"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
