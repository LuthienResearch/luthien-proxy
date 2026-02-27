"""Unit tests for ToolCallJudgePolicy.

Tests the unified policy's behavior for both OpenAI and Anthropic interfaces:
- OpenAI streaming chunk forwarding and buffering
- Anthropic streaming event filtering and buffering
- Tool call blocking/allowing based on judge evaluation
- Non-streaming response handling for both APIs
- Error handling and fail-secure behavior
- Stream completion and finish_reason handling
- Configuration and initialization

Note: Utility function tests are in test_tool_call_judge_utils.py
"""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, Mock, patch

import pytest
from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    RawMessageStartEvent,
    RawMessageStopEvent,
    TextBlock,
    TextDelta,
    ToolUseBlock,
)
from litellm.types.utils import (
    ChatCompletionDeltaToolCall,
    Delta,
    Function,
    ModelResponse,
)
from tests.unit_tests.helpers.litellm_test_utils import make_streaming_chunk

from conftest import DEFAULT_TEST_MODEL
from luthien_proxy.llm.types import Request
from luthien_proxy.llm.types.anthropic import (
    AnthropicRequest,
    AnthropicResponse,
    AnthropicTextBlock,
    AnthropicToolUseBlock,
)
from luthien_proxy.policies import PolicyContext
from luthien_proxy.policies.tool_call_judge_policy import ToolCallJudgeConfig, ToolCallJudgePolicy
from luthien_proxy.policies.tool_call_judge_utils import JudgeResult
from luthien_proxy.policy_core import (
    AnthropicPolicyInterface,
    OpenAIPolicyInterface,
)
from luthien_proxy.policy_core.chunk_builders import create_text_chunk
from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext
from luthien_proxy.streaming.stream_blocks import ToolCallStreamBlock
from luthien_proxy.streaming.stream_state import StreamState


def make_judge_result(probability: float, explanation: str = "test") -> JudgeResult:
    """Create a JudgeResult for testing."""
    return JudgeResult(
        probability=probability,
        explanation=explanation,
        prompt=[],
        response_text="",
    )


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

    # Egress queue
    # Use Mock (not AsyncMock) because put_nowait is sync, but make put async
    ctx.egress_queue = Mock()
    ctx.egress_queue.put_nowait = Mock()
    ctx.egress_queue.put = AsyncMock()

    return ctx


# ==============================================================================
# Protocol Compliance Tests
# ==============================================================================


class TestToolCallJudgePolicyProtocols:
    """Tests verifying ToolCallJudgePolicy implements the required interfaces."""

    def test_implements_openai_interface(self):
        """ToolCallJudgePolicy satisfies OpenAIPolicyInterface."""
        policy = ToolCallJudgePolicy()
        assert isinstance(policy, OpenAIPolicyInterface)

    def test_implements_anthropic_interface(self):
        """ToolCallJudgePolicy satisfies AnthropicPolicyInterface."""
        policy = ToolCallJudgePolicy()
        assert isinstance(policy, AnthropicPolicyInterface)

    def test_has_short_policy_name(self):
        """ToolCallJudgePolicy has correct short_policy_name property."""
        policy = ToolCallJudgePolicy()
        assert policy.short_policy_name == "ToolJudge"


# ==============================================================================
# Configuration Tests
# ==============================================================================


class TestToolCallJudgePolicyConfiguration:
    """Test policy configuration and initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default configuration."""
        policy = ToolCallJudgePolicy()

        assert policy._config.model == "claude-haiku-4-5"
        assert policy._config.probability_threshold == 0.6
        assert policy._config.temperature == 0.0
        assert policy._config.max_tokens == 256

    def test_init_with_custom_config(self):
        """Test initialization with custom configuration."""
        policy = ToolCallJudgePolicy(
            config=ToolCallJudgeConfig(
                model="claude-3-5-sonnet-20241022",
                api_base="http://custom:8000",
                api_key="test-key",
                probability_threshold=0.8,
                temperature=0.5,
                max_tokens=512,
                judge_instructions="Custom instructions",
                blocked_message_template="Custom template: {tool_name}",
            )
        )

        assert policy._config.model == "claude-3-5-sonnet-20241022"
        assert policy._config.api_base == "http://custom:8000"
        assert policy._config.api_key == "test-key"
        assert policy._config.probability_threshold == 0.8
        assert policy._config.temperature == 0.5
        assert policy._config.max_tokens == 512
        assert policy._judge_instructions == "Custom instructions"
        assert "Custom template" in policy._blocked_message_template

    def test_init_with_dict_config(self):
        """Test initialization with dict config (runtime policy manager path)."""
        policy = ToolCallJudgePolicy(
            config={
                "model": "claude-3-5-sonnet-20241022",
                "probability_threshold": 0.8,
            }
        )

        assert policy._config.model == "claude-3-5-sonnet-20241022"
        assert policy._config.probability_threshold == 0.8

    def test_init_invalid_threshold_raises(self):
        """Test that invalid probability threshold raises ValidationError."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ToolCallJudgePolicy(config=ToolCallJudgeConfig(probability_threshold=1.5))

        with pytest.raises(ValidationError):
            ToolCallJudgePolicy(config=ToolCallJudgeConfig(probability_threshold=-0.1))


# ==============================================================================
# OpenAI Interface Tests
# ==============================================================================


class TestToolCallJudgePolicyOpenAIContentForwarding:
    """Test that ToolCallJudgePolicy forwards content chunks (Bug #1 regression test)."""

    @pytest.mark.asyncio
    async def test_on_content_delta_forwards_chunks(self):
        """REGRESSION TEST: Verify content chunks are forwarded to egress.

        This test would have caught the bug where ToolCallJudgePolicy didn't
        implement on_content_delta, causing no chunks to reach the client.
        """
        policy = ToolCallJudgePolicy()

        # Create a content chunk
        content_chunk = make_streaming_chunk(content="hello world", model="test", id="test")

        ctx = create_mock_context(raw_chunks=[content_chunk])

        # Call on_content_delta
        await policy.on_content_delta(ctx)

        # CRITICAL: Verify chunk was forwarded to egress
        ctx.egress_queue.put_nowait.assert_called_once_with(content_chunk)

    @pytest.mark.asyncio
    async def test_on_content_delta_raises_on_empty_chunks(self):
        """Test that on_content_delta raises when called with no chunks.

        If on_content_delta is called when raw_chunks is empty, it indicates
        a bug in the calling code - fail fast rather than silently swallowing.
        """
        policy = ToolCallJudgePolicy()
        ctx = create_mock_context(raw_chunks=[])

        with pytest.raises(IndexError):
            await policy.on_content_delta(ctx)


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


class TestToolCallJudgePolicyOpenAIBlockedMessageChunks:
    """Test that blocked messages send separate content + finish chunks (Bug #3 regression test)."""

    @pytest.mark.asyncio
    async def test_blocked_tool_call_sends_two_chunks(self):
        """REGRESSION TEST: Verify blocked messages send content chunk + finish chunk separately.

        This test would have caught the bug where we sent content and finish_reason
        in a single chunk, causing the Anthropic SSE assembler to only process
        the content and miss the finish_reason, resulting in missing
        content_block_stop and message_delta events.
        """
        policy = ToolCallJudgePolicy(config=ToolCallJudgeConfig(probability_threshold=0.0))  # Block everything

        ctx = create_mock_context(transaction_id="test-call")

        # First, buffer the tool call using proper Delta object
        tc = ChatCompletionDeltaToolCall(
            id="call-123",
            type="function",
            index=0,
            function=Function(name="dangerous_action", arguments='{"confirm": true}'),
        )
        tool_call_chunk = make_streaming_chunk(content=None, model="test", id="test", tool_calls=[tc])
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
            return make_judge_result(probability=1.0, explanation="dangerous action")

        with patch("luthien_proxy.policies.tool_call_judge_policy.call_judge", side_effect=mock_call_judge):
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
    async def test_allowed_tool_call_cleanup_deferred(self):
        """Test that allowed tool calls defer cleanup to on_streaming_policy_complete."""
        policy = ToolCallJudgePolicy(config=ToolCallJudgeConfig(probability_threshold=1.0))  # Allow everything

        # Buffer a tool call first using proper Delta object
        tc = ChatCompletionDeltaToolCall(
            id="call-123",
            type="function",
            index=0,
            function=Function(name="safe_action", arguments='{"ok": true}'),
        )
        tool_call_chunk = make_streaming_chunk(content=None, model="test", id="test", tool_calls=[tc])

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

        with patch.object(policy, "_evaluate_and_maybe_block_openai", side_effect=mock_evaluate):
            await policy.on_tool_call_complete(ctx)

        # Verify buffer is NOT yet cleaned up
        assert ("test-call", 0) in policy._buffered_tool_calls

        # Now call cleanup
        await policy.on_streaming_policy_complete(ctx)

        # Verify buffer is NOW cleaned up
        assert ("test-call", 0) not in policy._buffered_tool_calls


class TestToolCallJudgePolicyOpenAIToolCallBuffering:
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
        chunk1 = make_streaming_chunk(content=None, model="test", id="test", tool_calls=[tc1])

        # Chunk 2: first part of arguments
        tc2 = ChatCompletionDeltaToolCall(
            index=0,
            function=Function(name=None, arguments='{"key":'),
        )
        chunk2 = make_streaming_chunk(content=None, model="test", id="test", tool_calls=[tc2])

        # Chunk 3: rest of arguments
        tc3 = ChatCompletionDeltaToolCall(
            index=0,
            function=Function(name=None, arguments='"value"}'),
        )
        chunk3 = make_streaming_chunk(content=None, model="test", id="test", tool_calls=[tc3])

        ctx = create_mock_context(transaction_id="test-call")

        # Process chunks
        for chunk in [chunk1, chunk2, chunk3]:
            ctx.original_streaming_response_state.raw_chunks = [chunk]
            await policy.on_tool_call_delta(ctx)

        # Verify buffered data
        key = ("test-call", 0)
        assert key in policy._buffered_tool_calls
        buffered = policy._buffered_tool_calls[key]

        assert buffered.id == "call-123"
        assert buffered.name == "test_tool"
        assert buffered.arguments == '{"key":"value"}'


class TestToolCallJudgePolicyOpenAINonStreaming:
    """Test non-streaming response handling."""

    @pytest.mark.asyncio
    async def test_on_openai_response_with_no_tool_calls(self):
        """Test that on_openai_response passes through responses with no tool calls."""
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
        )

        result = await policy.on_openai_response(response, ctx)

        # Should pass through unchanged
        assert result is response

    @pytest.mark.asyncio
    async def test_on_openai_response_blocks_harmful_tool_call(self):
        """Test that on_openai_response blocks tool calls judged as harmful."""
        from litellm.types.utils import ChatCompletionMessageToolCall

        policy = ToolCallJudgePolicy(config=ToolCallJudgeConfig(probability_threshold=0.5))

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
        )

        # Mock judge to block
        async def mock_call_judge(name, arguments, config, judge_instructions):
            return make_judge_result(probability=0.9, explanation="dangerous operation")

        with patch("luthien_proxy.policies.tool_call_judge_policy.call_judge", side_effect=mock_call_judge):
            result = await policy.on_openai_response(response, ctx)

        # Should return a blocked response, not the original
        assert result is not response
        assert result.choices[0].message.content is not None
        assert "BLOCKED" in result.choices[0].message.content

    @pytest.mark.asyncio
    async def test_on_openai_response_allows_safe_tool_call(self):
        """Test that on_openai_response allows tool calls judged as safe."""
        from litellm.types.utils import ChatCompletionMessageToolCall

        policy = ToolCallJudgePolicy(config=ToolCallJudgeConfig(probability_threshold=0.5))

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
        )

        # Mock judge to allow
        async def mock_call_judge(name, arguments, config, judge_instructions):
            return make_judge_result(probability=0.2, explanation="safe operation")

        with patch("luthien_proxy.policies.tool_call_judge_policy.call_judge", side_effect=mock_call_judge):
            result = await policy.on_openai_response(response, ctx)

        # Should pass through unchanged
        assert result is response


class TestToolCallJudgeOpenAIErrorHandling:
    """Test error handling in tool call judging for OpenAI."""

    @pytest.mark.asyncio
    async def test_judge_failure_blocks_tool_call(self):
        """Test that judge failures result in blocking (fail-secure)."""
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
        )

        # Mock judge to raise an exception
        async def mock_call_judge(*args, **kwargs):
            raise Exception("Judge service unavailable")

        with patch("luthien_proxy.policies.tool_call_judge_policy.call_judge", side_effect=mock_call_judge):
            result = await policy.on_openai_response(response, ctx)

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
        chunk = make_streaming_chunk(content=None, model="test", id="test", tool_calls=[tc])

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

        with patch("luthien_proxy.policies.tool_call_judge_policy.call_judge", side_effect=mock_evaluate):
            await policy.on_tool_call_complete(ctx)

        # Should have blocked by sending content + finish chunks
        assert ctx.egress_queue.put.call_count >= 1
        first_call = ctx.egress_queue.put.call_args_list[0]
        first_chunk = first_call[0][0]
        assert "SECURITY BLOCK" in str(first_chunk.choices[0].delta.content)


class TestToolCallJudgeOpenAIStreamComplete:
    """Test on_stream_complete hook."""

    @pytest.mark.asyncio
    async def test_on_stream_complete_emits_finish_for_tool_calls(self):
        """Test that on_stream_complete emits finish_reason for tool call responses."""
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


class TestOpenAIStreamingPolicyComplete:
    """Test on_streaming_policy_complete cleanup behavior."""

    @pytest.mark.asyncio
    async def test_cleanup_clears_buffered_tool_calls(self):
        """Test that cleanup removes buffered tool calls for the request."""
        policy = ToolCallJudgePolicy()
        ctx = create_mock_context(transaction_id="test-call-1")

        # Manually add some buffered tool calls
        policy._buffered_tool_calls[("test-call-1", 0)] = {
            "id": "call_abc",
            "type": "function",
            "name": "test_tool",
            "arguments": '{"arg": "value"}',
        }
        policy._buffered_tool_calls[("test-call-1", 1)] = {
            "id": "call_def",
            "type": "function",
            "name": "another_tool",
            "arguments": "{}",
        }
        # Add a buffer for a different call (should not be removed)
        policy._buffered_tool_calls[("other-call", 0)] = {
            "id": "call_xyz",
            "type": "function",
            "name": "other_tool",
            "arguments": "{}",
        }

        assert len(policy._buffered_tool_calls) == 3

        # Call cleanup
        await policy.on_streaming_policy_complete(ctx)

        # Only the current call's buffers should be removed
        assert len(policy._buffered_tool_calls) == 1
        assert ("other-call", 0) in policy._buffered_tool_calls
        assert ("test-call-1", 0) not in policy._buffered_tool_calls
        assert ("test-call-1", 1) not in policy._buffered_tool_calls

    @pytest.mark.asyncio
    async def test_cleanup_clears_blocked_calls(self):
        """Test that cleanup removes blocked call tracking for the request."""
        policy = ToolCallJudgePolicy()
        ctx = create_mock_context(transaction_id="test-call-1")

        # Mark some calls as blocked
        policy._blocked_calls.add("test-call-1")
        policy._blocked_calls.add("other-call")

        assert len(policy._blocked_calls) == 2

        # Call cleanup
        await policy.on_streaming_policy_complete(ctx)

        # Only the current call should be removed
        assert len(policy._blocked_calls) == 1
        assert "other-call" in policy._blocked_calls
        assert "test-call-1" not in policy._blocked_calls

    @pytest.mark.asyncio
    async def test_cleanup_handles_empty_buffers(self):
        """Test that cleanup handles case where there's nothing to clean up."""
        policy = ToolCallJudgePolicy()
        ctx = create_mock_context(transaction_id="test-call-1")

        # Should not raise any errors
        await policy.on_streaming_policy_complete(ctx)

        assert len(policy._buffered_tool_calls) == 0
        assert len(policy._blocked_calls) == 0


# ==============================================================================
# Anthropic Interface Tests
# ==============================================================================


class TestToolCallJudgePolicyAnthropicRequest:
    """Tests for on_anthropic_request passthrough behavior."""

    @pytest.mark.asyncio
    async def test_on_anthropic_request_returns_same_request(self):
        """on_anthropic_request returns the exact same request object unchanged."""
        policy = ToolCallJudgePolicy()
        ctx = PolicyContext.for_testing()

        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
        }

        result = await policy.on_anthropic_request(request, ctx)

        assert result is request

    @pytest.mark.asyncio
    async def test_on_anthropic_request_preserves_all_fields(self):
        """on_anthropic_request preserves all fields including tools."""
        policy = ToolCallJudgePolicy()
        ctx = PolicyContext.for_testing()

        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "What's the weather?"}],
            "max_tokens": 500,
            "tools": [
                {
                    "name": "get_weather",
                    "description": "Get weather for a location",
                    "input_schema": {
                        "type": "object",
                        "properties": {"location": {"type": "string"}},
                    },
                }
            ],
        }

        result = await policy.on_anthropic_request(request, ctx)

        assert result["model"] == DEFAULT_TEST_MODEL
        assert len(result.get("tools", [])) == 1


class TestToolCallJudgePolicyAnthropicResponseNoToolUse:
    """Tests for on_anthropic_response when there are no tool_use blocks."""

    @pytest.mark.asyncio
    async def test_on_anthropic_response_passthrough_text_only(self):
        """on_anthropic_response passes through responses with only text blocks."""
        policy = ToolCallJudgePolicy()
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "Hello, world!"}
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = await policy.on_anthropic_response(response, ctx)

        # Should pass through unchanged (same object reference possible)
        assert result["content"] == response["content"]
        assert result.get("stop_reason") == "end_turn"

    @pytest.mark.asyncio
    async def test_on_anthropic_response_empty_content(self):
        """on_anthropic_response handles empty content list."""
        policy = ToolCallJudgePolicy()
        ctx = PolicyContext.for_testing()

        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 0},
        }

        result = await policy.on_anthropic_response(response, ctx)

        assert result is response


class TestToolCallJudgePolicyAnthropicResponseToolUse:
    """Tests for on_anthropic_response with tool_use blocks."""

    @pytest.mark.asyncio
    async def test_on_anthropic_response_allows_safe_tool_call(self):
        """on_anthropic_response allows tool_use blocks judged as safe."""
        policy = ToolCallJudgePolicy(config=ToolCallJudgeConfig(probability_threshold=0.5))
        ctx = PolicyContext.for_testing()

        tool_use_block: AnthropicToolUseBlock = {
            "type": "tool_use",
            "id": "tool_123",
            "name": "get_weather",
            "input": {"location": "San Francisco"},
        }
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [tool_use_block],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        async def mock_call_judge(*args, **kwargs):
            return make_judge_result(probability=0.2, explanation="safe operation")

        with patch(
            "luthien_proxy.policies.tool_call_judge_policy.call_judge",
            side_effect=mock_call_judge,
        ):
            result = await policy.on_anthropic_response(response, ctx)

        # Tool use should be preserved
        result_tool_block = cast(AnthropicToolUseBlock, result["content"][0])
        assert result_tool_block["type"] == "tool_use"
        assert result_tool_block["name"] == "get_weather"
        assert result.get("stop_reason") == "tool_use"

    @pytest.mark.asyncio
    async def test_on_anthropic_response_blocks_harmful_tool_call(self):
        """on_anthropic_response blocks tool_use blocks judged as harmful."""
        policy = ToolCallJudgePolicy(config=ToolCallJudgeConfig(probability_threshold=0.5))
        ctx = PolicyContext.for_testing()

        tool_use_block: AnthropicToolUseBlock = {
            "type": "tool_use",
            "id": "tool_123",
            "name": "rm_rf",
            "input": {"path": "/"},
        }
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [tool_use_block],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        async def mock_call_judge(*args, **kwargs):
            return make_judge_result(probability=0.9, explanation="dangerous operation")

        with patch(
            "luthien_proxy.policies.tool_call_judge_policy.call_judge",
            side_effect=mock_call_judge,
        ):
            result = await policy.on_anthropic_response(response, ctx)

        # Tool use should be replaced with text
        result_block = cast(AnthropicTextBlock, result["content"][0])
        assert result_block["type"] == "text"
        assert "rm_rf" in result_block["text"]
        assert "rejected" in result_block["text"].lower()
        # stop_reason should change from tool_use to end_turn
        assert result.get("stop_reason") == "end_turn"

    @pytest.mark.asyncio
    async def test_on_anthropic_response_mixed_content_partial_block(self):
        """on_anthropic_response handles mixed content where only some tool_use is blocked."""
        policy = ToolCallJudgePolicy(config=ToolCallJudgeConfig(probability_threshold=0.5))
        ctx = PolicyContext.for_testing()

        text_block: AnthropicTextBlock = {"type": "text", "text": "Let me help you"}
        tool_use_block: AnthropicToolUseBlock = {
            "type": "tool_use",
            "id": "tool_123",
            "name": "dangerous_tool",
            "input": {"arg": "value"},
        }
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block, tool_use_block],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 15},
        }

        async def mock_call_judge(*args, **kwargs):
            return make_judge_result(probability=0.8, explanation="blocked")

        with patch(
            "luthien_proxy.policies.tool_call_judge_policy.call_judge",
            side_effect=mock_call_judge,
        ):
            result = await policy.on_anthropic_response(response, ctx)

        # Should have two text blocks now
        assert len(result["content"]) == 2
        result_block0 = cast(AnthropicTextBlock, result["content"][0])
        result_block1 = cast(AnthropicTextBlock, result["content"][1])
        assert result_block0["type"] == "text"
        assert result_block0["text"] == "Let me help you"
        assert result_block1["type"] == "text"
        assert "dangerous_tool" in result_block1["text"]


class TestToolCallJudgePolicyAnthropicErrorHandling:
    """Tests for error handling and fail-secure behavior for Anthropic."""

    @pytest.mark.asyncio
    async def test_anthropic_judge_failure_blocks_tool_call(self):
        """on_anthropic_response blocks when judge fails (fail-secure)."""
        policy = ToolCallJudgePolicy()
        ctx = PolicyContext.for_testing()

        tool_use_block: AnthropicToolUseBlock = {
            "type": "tool_use",
            "id": "tool_123",
            "name": "test_tool",
            "input": {},
        }
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [tool_use_block],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        async def mock_call_judge(*args, **kwargs):
            raise Exception("Judge service unavailable")

        with patch(
            "luthien_proxy.policies.tool_call_judge_policy.call_judge",
            side_effect=mock_call_judge,
        ):
            result = await policy.on_anthropic_response(response, ctx)

        # Should be blocked due to fail-secure
        result_block = cast(AnthropicTextBlock, result["content"][0])
        assert result_block["type"] == "text"
        assert "test_tool" in result_block["text"]


class TestToolCallJudgePolicyAnthropicStreamEventNonToolUse:
    """Tests for on_anthropic_stream_event with non-tool_use events."""

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_passes_through_message_start(self):
        """on_anthropic_stream_event passes through message_start events unchanged."""
        policy = ToolCallJudgePolicy()
        ctx = PolicyContext.for_testing()

        event = RawMessageStartEvent.model_construct(
            type="message_start",
            message={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": DEFAULT_TEST_MODEL,
                "stop_reason": None,
                "usage": {"input_tokens": 5, "output_tokens": 0},
            },
        )

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert result == [event]

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_passes_through_text_delta(self):
        """on_anthropic_stream_event passes through text_delta events unchanged."""
        policy = ToolCallJudgePolicy()
        ctx = PolicyContext.for_testing()

        text_delta = TextDelta.model_construct(type="text_delta", text="hello")
        event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=text_delta,
        )

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert result == [event]

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_passes_through_text_block_start(self):
        """on_anthropic_stream_event passes through text content_block_start unchanged."""
        policy = ToolCallJudgePolicy()
        ctx = PolicyContext.for_testing()

        event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=TextBlock.model_construct(type="text", text=""),
        )

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert result == [event]

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_passes_through_message_stop(self):
        """on_anthropic_stream_event passes through message_stop events unchanged."""
        policy = ToolCallJudgePolicy()
        ctx = PolicyContext.for_testing()

        event = RawMessageStopEvent.model_construct(type="message_stop")

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert result == [event]

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_passes_through_message_delta(self):
        """on_anthropic_stream_event passes through message_delta events unchanged."""
        policy = ToolCallJudgePolicy()
        ctx = PolicyContext.for_testing()

        event = RawMessageDeltaEvent.model_construct(
            type="message_delta",
            delta={"stop_reason": "end_turn", "stop_sequence": None},
            usage={"output_tokens": 10},
        )

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert result == [event]


class TestToolCallJudgePolicyAnthropicStreamEventToolUse:
    """Tests for on_anthropic_stream_event with tool_use events."""

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_buffers_tool_use_start(self):
        """on_anthropic_stream_event buffers tool_use content_block_start and returns None."""
        policy = ToolCallJudgePolicy()
        ctx = PolicyContext.for_testing()

        event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=ToolUseBlock.model_construct(
                type="tool_use",
                id="tool_123",
                name="get_weather",
                input={},
            ),
        )

        result = await policy.on_anthropic_stream_event(event, ctx)

        # Should filter out (return empty list) while buffering
        assert result == []
        # Should have buffered the data
        key = (ctx.transaction_id, 0)
        assert key in policy._buffered_tool_uses
        assert policy._buffered_tool_uses[key]["id"] == "tool_123"
        assert policy._buffered_tool_uses[key]["name"] == "get_weather"

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_buffers_input_json_delta(self):
        """on_anthropic_stream_event accumulates input_json_delta for buffered tool_use."""
        policy = ToolCallJudgePolicy()
        ctx = PolicyContext.for_testing()

        # First, start a tool_use block
        start_event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=ToolUseBlock.model_construct(
                type="tool_use",
                id="tool_123",
                name="get_weather",
                input={},
            ),
        )
        await policy.on_anthropic_stream_event(start_event, ctx)

        # Now send input_json_delta events
        delta1 = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=InputJSONDelta.model_construct(type="input_json_delta", partial_json='{"loc'),
        )
        delta2 = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=InputJSONDelta.model_construct(type="input_json_delta", partial_json='ation": "SF"}'),
        )

        result1 = await policy.on_anthropic_stream_event(delta1, ctx)
        result2 = await policy.on_anthropic_stream_event(delta2, ctx)

        # Should filter out both (return empty lists)
        assert result1 == []
        assert result2 == []
        # Should have accumulated the JSON
        key = (ctx.transaction_id, 0)
        assert policy._buffered_tool_uses[key]["input_json"] == '{"location": "SF"}'

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_judges_on_block_stop_allowed(self):
        """on_anthropic_stream_event judges tool call on content_block_stop and allows if safe."""
        policy = ToolCallJudgePolicy(config=ToolCallJudgeConfig(probability_threshold=0.5))
        ctx = PolicyContext.for_testing()

        # Buffer a complete tool call
        start_event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=ToolUseBlock.model_construct(
                type="tool_use",
                id="tool_123",
                name="get_weather",
                input={},
            ),
        )
        await policy.on_anthropic_stream_event(start_event, ctx)

        delta_event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=InputJSONDelta.model_construct(
                type="input_json_delta",
                partial_json='{"location": "SF"}',
            ),
        )
        await policy.on_anthropic_stream_event(delta_event, ctx)

        # Now send stop event with judge allowing
        stop_event = RawContentBlockStopEvent.model_construct(
            type="content_block_stop",
            index=0,
        )

        async def mock_call_judge(*args, **kwargs):
            return make_judge_result(probability=0.2, explanation="safe")

        with patch(
            "luthien_proxy.policies.tool_call_judge_policy.call_judge",
            side_effect=mock_call_judge,
        ):
            result = await policy.on_anthropic_stream_event(stop_event, ctx)

        # Should return reconstructed event sequence: [start, delta, stop]
        assert len(result) == 3
        # First event: reconstructed content_block_start with tool_use block
        assert isinstance(result[0], RawContentBlockStartEvent)
        assert result[0].index == 0
        assert isinstance(result[0].content_block, ToolUseBlock)
        assert result[0].content_block.name == "get_weather"
        assert result[0].content_block.id == "tool_123"
        # Second event: reconstructed content_block_delta with full JSON
        assert isinstance(result[1], RawContentBlockDeltaEvent)
        assert result[1].index == 0
        assert isinstance(result[1].delta, InputJSONDelta)
        assert result[1].delta.partial_json == '{"location": "SF"}'
        # Third event: the original stop event
        assert isinstance(result[2], RawContentBlockStopEvent)
        # Buffer should be cleared
        assert (ctx.transaction_id, 0) not in policy._buffered_tool_uses

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_judges_on_block_stop_blocked(self):
        """on_anthropic_stream_event judges tool call on content_block_stop and filters if blocked."""
        policy = ToolCallJudgePolicy(config=ToolCallJudgeConfig(probability_threshold=0.5))
        ctx = PolicyContext.for_testing()

        # Buffer a complete tool call
        start_event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=ToolUseBlock.model_construct(
                type="tool_use",
                id="tool_123",
                name="dangerous_tool",
                input={},
            ),
        )
        await policy.on_anthropic_stream_event(start_event, ctx)

        delta_event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=InputJSONDelta.model_construct(
                type="input_json_delta",
                partial_json='{"arg": "value"}',
            ),
        )
        await policy.on_anthropic_stream_event(delta_event, ctx)

        # Now send stop event with judge blocking
        stop_event = RawContentBlockStopEvent.model_construct(
            type="content_block_stop",
            index=0,
        )

        async def mock_call_judge(*args, **kwargs):
            return make_judge_result(probability=0.9, explanation="dangerous")

        with patch(
            "luthien_proxy.policies.tool_call_judge_policy.call_judge",
            side_effect=mock_call_judge,
        ):
            result = await policy.on_anthropic_stream_event(stop_event, ctx)

        # Should return replacement text events: [start, delta, stop]
        assert len(result) == 3
        # First event: text block start
        assert isinstance(result[0], RawContentBlockStartEvent)
        assert result[0].index == 0
        assert isinstance(result[0].content_block, TextBlock)
        # Second event: text delta with blocked message
        assert isinstance(result[1], RawContentBlockDeltaEvent)
        assert result[1].index == 0
        assert isinstance(result[1].delta, TextDelta)
        assert "dangerous_tool" in result[1].delta.text
        # Third event: the original stop event
        assert isinstance(result[2], RawContentBlockStopEvent)
        # Should have marked the block as blocked
        assert (ctx.transaction_id, 0) in policy._blocked_blocks
        # Buffer should be cleared
        assert (ctx.transaction_id, 0) not in policy._buffered_tool_uses


class TestToolCallJudgePolicyAnthropicStreamingErrorHandling:
    """Tests for streaming error handling for Anthropic."""

    @pytest.mark.asyncio
    async def test_on_anthropic_stream_event_judge_failure_blocks(self):
        """on_anthropic_stream_event blocks on judge failure (fail-secure)."""
        policy = ToolCallJudgePolicy()
        ctx = PolicyContext.for_testing()

        # Buffer a tool call
        start_event = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=ToolUseBlock.model_construct(
                type="tool_use",
                id="tool_123",
                name="test_tool",
                input={},
            ),
        )
        await policy.on_anthropic_stream_event(start_event, ctx)

        delta_event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta",
            index=0,
            delta=InputJSONDelta.model_construct(type="input_json_delta", partial_json="{}"),
        )
        await policy.on_anthropic_stream_event(delta_event, ctx)

        stop_event = RawContentBlockStopEvent.model_construct(
            type="content_block_stop",
            index=0,
        )

        async def mock_call_judge(*args, **kwargs):
            raise Exception("Judge failure")

        with patch(
            "luthien_proxy.policies.tool_call_judge_policy.call_judge",
            side_effect=mock_call_judge,
        ):
            result = await policy.on_anthropic_stream_event(stop_event, ctx)

        # Should return replacement text events due to fail-secure blocking
        assert len(result) == 3
        assert isinstance(result[0], RawContentBlockStartEvent)
        assert isinstance(result[0].content_block, TextBlock)
        assert isinstance(result[1], RawContentBlockDeltaEvent)
        assert isinstance(result[1].delta, TextDelta)
        assert "test_tool" in result[1].delta.text
        assert isinstance(result[2], RawContentBlockStopEvent)
        assert (ctx.transaction_id, 0) in policy._blocked_blocks

    @pytest.mark.asyncio
    async def test_on_anthropic_streaming_policy_complete_cleans_only_current_transaction(self):
        """Cleanup removes only current transaction's Anthropic streaming buffers."""
        policy = ToolCallJudgePolicy()
        ctx_a = PolicyContext.for_testing(transaction_id="txn-a")
        ctx_b = PolicyContext.for_testing(transaction_id="txn-b")

        start_a = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=ToolUseBlock.model_construct(type="tool_use", id="tool_a", name="safe_tool", input={}),
        )
        start_b = RawContentBlockStartEvent.model_construct(
            type="content_block_start",
            index=0,
            content_block=ToolUseBlock.model_construct(type="tool_use", id="tool_b", name="safe_tool", input={}),
        )
        await policy.on_anthropic_stream_event(start_a, ctx_a)
        await policy.on_anthropic_stream_event(start_b, ctx_b)

        policy._blocked_blocks.add(("txn-a", 0))
        policy._blocked_blocks.add(("txn-b", 0))

        await policy.on_anthropic_streaming_policy_complete(ctx_a)

        assert ("txn-a", 0) not in policy._buffered_tool_uses
        assert ("txn-b", 0) in policy._buffered_tool_uses
        assert ("txn-a", 0) not in policy._blocked_blocks
        assert ("txn-b", 0) in policy._blocked_blocks


class CapturingEmitter:
    """Event emitter that captures events for testing."""

    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []

    def record(self, transaction_id: str, event_type: str, data: dict) -> None:
        self.events.append((transaction_id, event_type, data))


class TestToolCallJudgePolicyObservability:
    """Tests for observability event emission."""

    @pytest.mark.asyncio
    async def test_openai_emits_evaluation_events(self):
        """Policy emits observability events during OpenAI evaluation."""
        from litellm.types.utils import ChatCompletionMessageToolCall

        policy = ToolCallJudgePolicy(config=ToolCallJudgeConfig(probability_threshold=0.5))

        # Use a capturing emitter to verify events
        emitter = CapturingEmitter()
        ctx = PolicyContext(
            transaction_id="test-txn",
            emitter=emitter,
        )

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
                                function=Function(name="test_tool", arguments='{"arg": "value"}'),
                            )
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        )

        async def mock_call_judge(*args, **kwargs):
            return make_judge_result(probability=0.2, explanation="safe")

        with patch(
            "luthien_proxy.policies.tool_call_judge_policy.call_judge",
            side_effect=mock_call_judge,
        ):
            await policy.on_openai_response(response, ctx)

        # Check that events were emitted (OpenAI uses no prefix)
        event_types = [e[1] for e in emitter.events]
        assert "policy.judge.evaluation_started" in event_types
        assert "policy.judge.evaluation_complete" in event_types
        assert "policy.judge.tool_call_allowed" in event_types

    @pytest.mark.asyncio
    async def test_anthropic_emits_evaluation_events(self):
        """Policy emits observability events during Anthropic evaluation."""
        policy = ToolCallJudgePolicy(config=ToolCallJudgeConfig(probability_threshold=0.5))

        # Use a capturing emitter to verify events
        emitter = CapturingEmitter()
        ctx = PolicyContext(
            transaction_id="test-txn",
            emitter=emitter,
        )

        tool_use_block: AnthropicToolUseBlock = {
            "type": "tool_use",
            "id": "tool_123",
            "name": "test_tool",
            "input": {"arg": "value"},
        }
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [tool_use_block],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        async def mock_call_judge(*args, **kwargs):
            return make_judge_result(probability=0.2, explanation="safe")

        with patch(
            "luthien_proxy.policies.tool_call_judge_policy.call_judge",
            side_effect=mock_call_judge,
        ):
            await policy.on_anthropic_response(response, ctx)

        # Check that events were emitted (Anthropic uses anthropic_ prefix)
        event_types = [e[1] for e in emitter.events]
        assert "policy.anthropic_judge.evaluation_started" in event_types
        assert "policy.anthropic_judge.evaluation_complete" in event_types
        assert "policy.anthropic_judge.tool_call_allowed" in event_types


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
