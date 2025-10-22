# ABOUTME: Unit tests for ToolCallJudgePolicy
# ABOUTME: Tests judge evaluation, blocking, and event emission

"""Unit tests for ToolCallJudgePolicy."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from litellm.types.utils import Choices, Message, ModelResponse

from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.policies.context import PolicyContext
from luthien_proxy.v2.policies.tool_call_judge import ToolCallJudgePolicy
from luthien_proxy.v2.policies.utils import chunk_contains_tool_call, is_tool_call_complete


@pytest.fixture
def mock_context():
    """Create a mock PolicyContext."""
    context = MagicMock(spec=PolicyContext)
    context.call_id = "test-call-123"
    context.emit = MagicMock()
    return context


@pytest.fixture
def judge_policy():
    """Create a ToolCallJudgePolicy with test configuration."""
    return ToolCallJudgePolicy(
        model="test-model",
        api_base="http://test-judge:8080",
        api_key="test-key",
        probability_threshold=0.6,
        temperature=0.0,
        max_tokens=128,
    )


class TestInitialization:
    """Test policy initialization and configuration."""

    def test_default_initialization(self):
        """Test policy initializes with defaults."""
        policy = ToolCallJudgePolicy()
        assert policy._config.model == "openai/judge-scorer"
        assert policy._config.probability_threshold == 0.6
        assert policy._config.temperature == 0.0
        assert policy._config.max_tokens == 256

    def test_custom_configuration(self):
        """Test policy initializes with custom config."""
        policy = ToolCallJudgePolicy(
            model="custom-model",
            api_base="http://custom:8080",
            probability_threshold=0.8,
            temperature=0.5,
            max_tokens=512,
        )
        assert policy._config.model == "custom-model"
        assert policy._config.api_base == "http://custom:8080"
        assert policy._config.probability_threshold == 0.8
        assert policy._config.temperature == 0.5
        assert policy._config.max_tokens == 512

    def test_invalid_threshold_raises(self):
        """Test that invalid threshold raises ValueError."""
        with pytest.raises(ValueError, match="probability_threshold must be between 0 and 1"):
            ToolCallJudgePolicy(probability_threshold=1.5)

        with pytest.raises(ValueError, match="probability_threshold must be between 0 and 1"):
            ToolCallJudgePolicy(probability_threshold=-0.1)


class TestProcessRequest:
    """Test request processing (should be passthrough)."""

    async def test_request_passthrough(self, judge_policy, mock_context):
        """Test that requests pass through unchanged."""
        request = Request(
            model="gpt-4",
            messages=[{"role": "user", "content": "test"}],
        )

        result = await judge_policy.process_request(request, mock_context)

        assert result == request
        mock_context.emit.assert_called_once_with(
            "judge.request_passthrough", "Request passed through without modification"
        )


class TestProcessFullResponse:
    """Test non-streaming response processing."""

    async def test_no_tool_calls_passthrough(self, judge_policy, mock_context):
        """Test response without tool calls passes through."""
        response = ModelResponse(
            id="test-123",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(content="Hello!", role="assistant"),
                )
            ],
            created=123456,
            model="gpt-4",
            object="chat.completion",
        )

        result = await judge_policy.process_full_response(response, mock_context)

        assert result == response
        assert any("no_tool_calls" in str(call) for call in mock_context.emit.call_args_list)

    @patch("luthien_proxy.v2.policies.tool_call_judge.acompletion")
    async def test_tool_call_below_threshold_passes(self, mock_acompletion, judge_policy, mock_context):
        """Test tool call with low probability passes through."""
        # Mock judge response (low probability)
        mock_acompletion.return_value = MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(content='{"probability": 0.2, "explanation": "Looks safe"}'),
                )
            ]
        )

        # Response with tool call
        from litellm.types.utils import ChatCompletionMessageToolCall, Function

        response = ModelResponse(
            id="test-123",
            choices=[
                Choices(
                    finish_reason="tool_calls",
                    index=0,
                    message=Message(
                        role="assistant",
                        content=None,
                        tool_calls=[
                            ChatCompletionMessageToolCall(
                                id="call-123",
                                type="function",
                                function=Function(name="get_weather", arguments='{"location": "SF"}'),
                            )
                        ],
                    ),
                )
            ],
            created=123456,
            model="gpt-4",
            object="chat.completion",
        )

        result = await judge_policy.process_full_response(response, mock_context)

        # Should return original response
        assert result == response
        assert any("all_passed" in str(call) for call in mock_context.emit.call_args_list)

    @patch("luthien_proxy.v2.policies.tool_call_judge.acompletion")
    async def test_tool_call_above_threshold_blocks(self, mock_acompletion, judge_policy, mock_context):
        """Test tool call with high probability gets blocked."""
        # Mock judge response (high probability)
        mock_acompletion.return_value = MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(content='{"probability": 0.9, "explanation": "Dangerous operation"}'),
                )
            ]
        )

        # Response with tool call
        from litellm.types.utils import ChatCompletionMessageToolCall, Function

        response = ModelResponse(
            id="test-123",
            choices=[
                Choices(
                    finish_reason="tool_calls",
                    index=0,
                    message=Message(
                        role="assistant",
                        content=None,
                        tool_calls=[
                            ChatCompletionMessageToolCall(
                                id="call-123",
                                type="function",
                                function=Function(name="delete_database", arguments='{"confirm": true}'),
                            )
                        ],
                    ),
                )
            ],
            created=123456,
            model="gpt-4",
            object="chat.completion",
        )

        result = await judge_policy.process_full_response(response, mock_context)

        # Should return blocked response
        assert result != response
        assert result.choices[0].message.content
        assert "BLOCKED" in result.choices[0].message.content
        assert "delete_database" in result.choices[0].message.content

        # Check events emitted
        assert any("blocking" in str(call) for call in mock_context.emit.call_args_list)


class TestJudgePromptParsing:
    """Test judge prompt building and response parsing."""

    def test_build_judge_prompt(self, judge_policy):
        """Test judge prompt is built correctly."""
        prompt = judge_policy._build_judge_prompt("test_function", '{"arg": "value"}')

        assert len(prompt) == 2
        assert prompt[0]["role"] == "system"
        assert "security analyst" in prompt[0]["content"].lower()
        assert prompt[1]["role"] == "user"
        assert "test_function" in prompt[1]["content"]
        assert '{"arg": "value"}' in prompt[1]["content"]

    def test_parse_valid_json_response(self, judge_policy):
        """Test parsing valid JSON response."""
        content = '{"probability": 0.7, "explanation": "Risky operation"}'
        result = judge_policy._parse_judge_response(content)

        assert result["probability"] == 0.7
        assert result["explanation"] == "Risky operation"

    def test_parse_fenced_json_response(self, judge_policy):
        """Test parsing JSON in fenced code block."""
        content = '```json\n{"probability": 0.5, "explanation": "Moderate risk"}\n```'
        result = judge_policy._parse_judge_response(content)

        assert result["probability"] == 0.5
        assert result["explanation"] == "Moderate risk"

    def test_parse_invalid_json_raises(self, judge_policy):
        """Test that invalid JSON raises ValueError."""
        with pytest.raises(ValueError, match="JSON parsing failed"):
            judge_policy._parse_judge_response("not valid json")


class TestToolCallDetection:
    """Test tool call detection in chunks.

    NOTE: These tests now use the utility functions directly.
    The policy methods have been moved to utils.py.
    """

    def test_chunk_contains_tool_call_in_delta(self):
        """Test detecting tool call in delta."""
        chunk = {"choices": [{"delta": {"tool_calls": [{"id": "call-123", "function": {"name": "test"}}]}}]}

        assert chunk_contains_tool_call(chunk) is True

    def test_chunk_contains_tool_call_in_message(self):
        """Test detecting tool call in message."""
        chunk = {"choices": [{"message": {"tool_calls": [{"id": "call-123", "function": {"name": "test"}}]}}]}

        assert chunk_contains_tool_call(chunk) is True

    def test_chunk_without_tool_call(self):
        """Test chunk without tool calls returns False."""
        chunk = {"choices": [{"delta": {"content": "Hello"}}]}

        assert chunk_contains_tool_call(chunk) is False

    def test_is_tool_call_complete_with_finish_reason(self):
        """Test detecting complete tool call via finish_reason."""
        chunk = {"choices": [{"finish_reason": "tool_calls"}]}

        assert is_tool_call_complete(chunk) is True

    def test_is_tool_call_complete_with_message(self):
        """Test detecting complete tool call via message."""
        chunk = {"choices": [{"message": {"tool_calls": [{"id": "call-123", "function": {"name": "test"}}]}}]}

        assert is_tool_call_complete(chunk) is True

    def test_is_tool_call_not_complete(self):
        """Test incomplete tool call returns False."""
        chunk = {"choices": [{"delta": {"tool_calls": [{"index": 0}]}}]}

        assert is_tool_call_complete(chunk) is False


class TestStreamingProcessing:
    """Test streaming response processing."""

    @patch("luthien_proxy.v2.policies.tool_call_judge.acompletion")
    async def test_streaming_no_tool_calls_passthrough(self, mock_acompletion, judge_policy, mock_context):
        """Test streaming without tool calls passes through."""
        incoming = asyncio.Queue()
        outgoing = asyncio.Queue()

        # Add text chunks
        chunks = [
            ModelResponse(
                id="test",
                choices=[Choices(index=0, delta={"role": "assistant", "content": "Hello "}, finish_reason=None)],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
            ModelResponse(
                id="test",
                choices=[Choices(index=0, delta={"content": "world"}, finish_reason=None)],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
            ModelResponse(
                id="test",
                choices=[Choices(index=0, delta={}, finish_reason="stop")],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
        ]

        for chunk in chunks:
            await incoming.put(chunk)
        incoming.shutdown()

        # Process stream
        await judge_policy.process_streaming_response(incoming, outgoing, mock_context)

        # All chunks should pass through
        output_chunks = []
        while True:
            try:
                output_chunks.append(outgoing.get_nowait())
            except (asyncio.QueueEmpty, asyncio.QueueShutDown):
                break

        assert len(output_chunks) == 3
        assert not mock_acompletion.called  # Judge should not be called

    async def test_stream_end_with_incomplete_tool_call_blocks(self, judge_policy, mock_context):
        """Test that incomplete tool call at stream end is blocked (fail-safe)."""
        incoming = asyncio.Queue()
        outgoing = asyncio.Queue()

        # Stream with incomplete tool call (missing name and arguments)
        chunks = [
            ModelResponse(
                id="test",
                choices=[
                    Choices(
                        index=0,
                        delta={
                            "role": "assistant",
                            "tool_calls": [{"index": 0, "id": "call_123", "type": "function", "function": {}}],
                        },
                        finish_reason=None,
                    )
                ],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
            # Stream ends here without completing tool call name/arguments
        ]

        for chunk in chunks:
            await incoming.put(chunk)
        incoming.shutdown()

        # Process stream
        await judge_policy.process_streaming_response(incoming, outgoing, mock_context)

        # Should get blocked response
        output_chunks = []
        while True:
            try:
                output_chunks.append(outgoing.get_nowait())
            except (asyncio.QueueEmpty, asyncio.QueueShutDown):
                break

        assert len(output_chunks) == 1
        blocked_chunk = output_chunks[0]
        assert blocked_chunk.choices[0].message.content.startswith("⛔ BLOCKED: Incomplete tool call")
        assert "fail-safe" in blocked_chunk.choices[0].message.content

        # Verify events
        emit_calls = [call[0][0] for call in mock_context.emit.call_args_list]
        assert "judge.stream_ended_with_buffer" in emit_calls
        assert "judge.incomplete_tool_call" in emit_calls
        assert "judge.blocked_on_stream_end" in emit_calls

    @patch("luthien_proxy.v2.policies.tool_call_judge.acompletion")
    async def test_stream_end_with_complete_tool_call_evaluates(self, mock_acompletion, judge_policy, mock_context):
        """Test that complete tool call at stream end is evaluated by judge."""
        # Mock judge response - allow the tool call
        mock_acompletion.return_value = ModelResponse(
            id="judge-response",
            choices=[
                Choices(
                    index=0,
                    message=Message(role="assistant", content='{"probability": 0.3, "explanation": "Safe tool call"}'),
                    finish_reason="stop",
                )
            ],
            created=123,
            model="test-model",
        )

        incoming = asyncio.Queue()
        outgoing = asyncio.Queue()

        # Stream with complete tool call
        chunks = [
            ModelResponse(
                id="test",
                choices=[
                    Choices(
                        index=0,
                        delta={
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_123",
                                    "type": "function",
                                    "function": {"name": "get_weather"},
                                }
                            ],
                        },
                        finish_reason=None,
                    )
                ],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
            ModelResponse(
                id="test",
                choices=[
                    Choices(
                        index=0,
                        delta={"tool_calls": [{"index": 0, "function": {"arguments": '{"location":'}}]},
                        finish_reason=None,
                    )
                ],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
            ModelResponse(
                id="test",
                choices=[
                    Choices(
                        index=0,
                        delta={"tool_calls": [{"index": 0, "function": {"arguments": ' "NYC"}'}}]},
                        finish_reason=None,
                    )
                ],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
            # Stream ends here - tool call is complete but stream ends without finish_reason
        ]

        for chunk in chunks:
            await incoming.put(chunk)
        incoming.shutdown()

        # Process stream
        await judge_policy.process_streaming_response(incoming, outgoing, mock_context)

        # Should evaluate and replay buffered chunks
        output_chunks = []
        while True:
            try:
                output_chunks.append(outgoing.get_nowait())
            except (asyncio.QueueEmpty, asyncio.QueueShutDown):
                break

        # All 3 chunks should be replayed
        assert len(output_chunks) == 3
        assert mock_acompletion.called

        # Verify events
        emit_calls = [call[0][0] for call in mock_context.emit.call_args_list]
        assert "judge.stream_ended_with_buffer" in emit_calls
        assert "judge.passed_on_stream_end" in emit_calls

    @patch("luthien_proxy.v2.policies.tool_call_judge.acompletion")
    async def test_content_after_tool_call_is_buffered(self, mock_acompletion, judge_policy, mock_context):
        """Test that content chunks arriving after tool call start are buffered.

        This is a regression test for a bug where non-tool-call content
        after a tool call started would not be buffered properly.
        """
        # Mock judge response - allow the tool call
        mock_acompletion.return_value = ModelResponse(
            id="judge-response",
            choices=[
                Choices(
                    index=0,
                    message=Message(role="assistant", content='{"probability": 0.2, "explanation": "Safe"}'),
                    finish_reason="stop",
                )
            ],
            created=123,
            model="test-model",
        )

        incoming = asyncio.Queue()
        outgoing = asyncio.Queue()

        # Stream: tool_call chunk -> content chunk -> finish chunk
        # All should be buffered until tool call is complete
        chunks = [
            ModelResponse(
                id="test",
                choices=[
                    Choices(
                        index=0,
                        delta={
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_123",
                                    "type": "function",
                                    "function": {"name": "search", "arguments": '{"q": "test"}'},
                                }
                            ],
                        },
                        finish_reason=None,
                    )
                ],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
            ModelResponse(
                id="test",
                choices=[Choices(index=0, delta={"content": "Here are the results:"}, finish_reason=None)],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
            ModelResponse(
                id="test",
                choices=[Choices(index=0, delta={}, finish_reason="tool_calls")],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
        ]

        for chunk in chunks:
            await incoming.put(chunk)
        incoming.shutdown()

        # Process stream
        await judge_policy.process_streaming_response(incoming, outgoing, mock_context)

        # Collect output
        output_chunks = []
        while True:
            try:
                output_chunks.append(outgoing.get_nowait())
            except (asyncio.QueueEmpty, asyncio.QueueShutDown):
                break

        # All 3 chunks should be replayed (tool call approved)
        assert len(output_chunks) == 3

        # Verify the content chunk is present in output
        has_content_chunk = any(
            hasattr(chunk.choices[0], "delta") and chunk.choices[0].delta.get("content") == "Here are the results:"
            for chunk in output_chunks
        )
        assert has_content_chunk, "Content chunk after tool call should be buffered and replayed"

        # Judge should have been called
        assert mock_acompletion.called

    @patch("luthien_proxy.v2.policies.tool_call_judge.acompletion")
    async def test_buffer_state_machine_simple(self, mock_acompletion, judge_policy, mock_context):
        """Test the simple buffer state machine: buffer, judge, forward or block.

        Desired behavior (simple state machine):
        1. Buffer all incoming chunks
        2. While first item in buffer is not a tool call, forward it
        3. When a complete tool call is at the front of buffer, judge it
        4. If approved: forward the tool call chunks, goto 2
        5. If rejected: send rejection, end stream

        This test verifies that:
        - Text content is forwarded immediately (not buffered with tool calls)
        - Each tool call is judged before being forwarded
        - Approved tool calls are forwarded
        - Rejected tool calls cause stream termination
        """
        judge_call_count = 0

        async def mock_judge(*args, **kwargs):
            nonlocal judge_call_count
            judge_call_count += 1

            # First call (tool A) - approve
            if judge_call_count == 1:
                probability = 0.2
                explanation = "Tool A is safe"
            # Second call (tool B) - BLOCK
            else:
                probability = 0.9
                explanation = "Tool B is dangerous"

            return ModelResponse(
                id=f"judge-response-{judge_call_count}",
                choices=[
                    Choices(
                        index=0,
                        message=Message(
                            role="assistant",
                            content=f'{{"probability": {probability}, "explanation": "{explanation}"}}',
                        ),
                        finish_reason="stop",
                    )
                ],
                created=123,
                model="test-model",
            )

        mock_acompletion.side_effect = mock_judge

        incoming = asyncio.Queue()
        outgoing = asyncio.Queue()

        # Stream: text -> tool A (approve) -> text -> tool B (block)
        chunks = [
            # Initial text content (should forward immediately)
            ModelResponse(
                id="test",
                choices=[Choices(index=0, delta={"role": "assistant", "content": "Let me help: "}, finish_reason=None)],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
            # Tool call A (should buffer, judge, forward)
            ModelResponse(
                id="test",
                choices=[
                    Choices(
                        index=0,
                        delta={
                            "tool_calls": [
                                {"index": 0, "id": "call_A", "type": "function", "function": {"name": "safe_function"}}
                            ]
                        },
                        finish_reason=None,
                    )
                ],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
            ModelResponse(
                id="test",
                choices=[
                    Choices(
                        index=0,
                        delta={"tool_calls": [{"index": 0, "function": {"arguments": '{"param": "value"}'}}]},
                        finish_reason=None,
                    )
                ],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
            ModelResponse(
                id="test",
                choices=[Choices(index=0, delta={}, finish_reason="tool_calls")],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
            # More text content (should forward immediately after tool A approval)
            ModelResponse(
                id="test",
                choices=[Choices(index=0, delta={"content": "Done. Now: "}, finish_reason=None)],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
            # Tool call B (should buffer, judge, BLOCK and terminate)
            ModelResponse(
                id="test",
                choices=[
                    Choices(
                        index=0,
                        delta={
                            "tool_calls": [
                                {
                                    "index": 1,
                                    "id": "call_B",
                                    "type": "function",
                                    "function": {"name": "dangerous_function"},
                                }
                            ]
                        },
                        finish_reason=None,
                    )
                ],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
            ModelResponse(
                id="test",
                choices=[
                    Choices(
                        index=0,
                        delta={"tool_calls": [{"index": 1, "function": {"arguments": '{"destructive": true}'}}]},
                        finish_reason=None,
                    )
                ],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
            ModelResponse(
                id="test",
                choices=[Choices(index=0, delta={}, finish_reason="tool_calls")],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
        ]

        for chunk in chunks:
            await incoming.put(chunk)
        incoming.shutdown()

        # Process stream
        await judge_policy.process_streaming_response(incoming, outgoing, mock_context)

        # Collect output
        output_chunks = []
        while True:
            try:
                output_chunks.append(outgoing.get_nowait())
            except (asyncio.QueueEmpty, asyncio.QueueShutDown):
                break

        # ASSERTIONS:
        # 1. Judge should be called TWICE (once for A, once for B)
        assert judge_call_count == 2, f"Expected 2 judge calls, got {judge_call_count}"

        # 2. Output should contain:
        #    - Initial text "Let me help: "
        #    - Tool A chunks (3 chunks)
        #    - Text "Done. Now: "
        #    - Blocked response for tool B
        #    Total: 1 + 3 + 1 + 1 = 6 chunks minimum

        # Find the blocked response
        blocked_chunks = [
            c
            for c in output_chunks
            if hasattr(c.choices[0], "message") and "BLOCKED" in str(c.choices[0].message.content)
        ]
        assert len(blocked_chunks) == 1, "Should have exactly one blocked response"
        assert "dangerous_function" in blocked_chunks[0].choices[0].message.content

        # Verify text chunks are present
        text_chunks = [
            c
            for c in output_chunks
            if hasattr(c.choices[0], "delta")
            and c.choices[0].delta.get("content")
            and "Let me help" in c.choices[0].delta.get("content", "")
        ]
        assert len(text_chunks) >= 1, "Initial text should be forwarded immediately"

        # Verify tool A was forwarded
        tool_a_chunks = [
            c
            for c in output_chunks
            if hasattr(c.choices[0], "delta")
            and c.choices[0].delta.get("tool_calls")
            and any(tc.get("id") == "call_A" for tc in c.choices[0].delta.get("tool_calls", []))
        ]
        assert len(tool_a_chunks) > 0, "Tool A chunks should be present (it was approved)"

        # Verify tool B chunks are NOT in output (blocked before forwarding)
        tool_b_chunks = [
            c
            for c in output_chunks
            if hasattr(c.choices[0], "delta")
            and c.choices[0].delta.get("tool_calls")
            and any(tc.get("id") == "call_B" for tc in c.choices[0].delta.get("tool_calls", []))
        ]
        assert len(tool_b_chunks) == 0, "Tool B chunks should NOT be forwarded (it was blocked)"

    @patch("luthien_proxy.v2.policies.tool_call_judge.acompletion")
    async def test_interleaved_tool_calls_and_text(self, mock_acompletion, judge_policy, mock_context):
        """Test pattern: tool call -> text -> tool call -> text -> tool call.

        This tests the state machine handles complex interleaving correctly.
        Each element should be forwarded in order after approval.
        """
        judge_call_count = 0

        async def mock_judge(*args, **kwargs):
            nonlocal judge_call_count
            judge_call_count += 1
            # Approve all tool calls
            return ModelResponse(
                id=f"judge-response-{judge_call_count}",
                choices=[
                    Choices(
                        index=0,
                        message=Message(role="assistant", content='{"probability": 0.1, "explanation": "Safe"}'),
                        finish_reason="stop",
                    )
                ],
                created=123,
                model="test-model",
            )

        mock_acompletion.side_effect = mock_judge

        incoming = asyncio.Queue()
        outgoing = asyncio.Queue()

        # Pattern: tool1 -> text1 -> tool2 -> text2 -> tool3
        chunks = [
            # Tool call 1
            ModelResponse(
                id="test",
                choices=[
                    Choices(
                        index=0,
                        delta={
                            "role": "assistant",
                            "tool_calls": [
                                {"index": 0, "id": "call_1", "type": "function", "function": {"name": "func1"}}
                            ],
                        },
                        finish_reason=None,
                    )
                ],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
            ModelResponse(
                id="test",
                choices=[
                    Choices(
                        index=0,
                        delta={"tool_calls": [{"index": 0, "function": {"arguments": '{"a": 1}'}}]},
                        finish_reason=None,
                    )
                ],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
            ModelResponse(
                id="test",
                choices=[Choices(index=0, delta={}, finish_reason="tool_calls")],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
            # Text 1
            ModelResponse(
                id="test",
                choices=[Choices(index=0, delta={"content": "Result 1. "}, finish_reason=None)],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
            # Tool call 2
            ModelResponse(
                id="test",
                choices=[
                    Choices(
                        index=0,
                        delta={
                            "tool_calls": [
                                {"index": 1, "id": "call_2", "type": "function", "function": {"name": "func2"}}
                            ]
                        },
                        finish_reason=None,
                    )
                ],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
            ModelResponse(
                id="test",
                choices=[
                    Choices(
                        index=0,
                        delta={"tool_calls": [{"index": 1, "function": {"arguments": '{"b": 2}'}}]},
                        finish_reason=None,
                    )
                ],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
            ModelResponse(
                id="test",
                choices=[Choices(index=0, delta={}, finish_reason="tool_calls")],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
            # Text 2
            ModelResponse(
                id="test",
                choices=[Choices(index=0, delta={"content": "Result 2. "}, finish_reason=None)],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
            # Tool call 3
            ModelResponse(
                id="test",
                choices=[
                    Choices(
                        index=0,
                        delta={
                            "tool_calls": [
                                {"index": 2, "id": "call_3", "type": "function", "function": {"name": "func3"}}
                            ]
                        },
                        finish_reason=None,
                    )
                ],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
            ModelResponse(
                id="test",
                choices=[
                    Choices(
                        index=0,
                        delta={"tool_calls": [{"index": 2, "function": {"arguments": '{"c": 3}'}}]},
                        finish_reason=None,
                    )
                ],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
            ModelResponse(
                id="test",
                choices=[Choices(index=0, delta={}, finish_reason="tool_calls")],
                created=123,
                model="gpt-4",
                object="chat.completion.chunk",
            ),
        ]

        for chunk in chunks:
            await incoming.put(chunk)
        incoming.shutdown()

        # Process stream
        await judge_policy.process_streaming_response(incoming, outgoing, mock_context)

        # Collect output
        output_chunks = []
        while True:
            try:
                output_chunks.append(outgoing.get_nowait())
            except (asyncio.QueueEmpty, asyncio.QueueShutDown):
                break

        # ASSERTIONS:
        # 1. Judge should be called 3 times (once per tool call)
        assert judge_call_count == 3, f"Expected 3 judge calls, got {judge_call_count}"

        # 2. All chunks should be in output (all approved)
        assert len(output_chunks) == len(chunks), f"Expected {len(chunks)} output chunks, got {len(output_chunks)}"

        # 3. Verify each tool call is present
        for call_id in ["call_1", "call_2", "call_3"]:
            tool_chunks = [
                c
                for c in output_chunks
                if hasattr(c.choices[0], "delta")
                and c.choices[0].delta.get("tool_calls")
                and any(tc.get("id") == call_id for tc in c.choices[0].delta.get("tool_calls", []))
            ]
            assert len(tool_chunks) > 0, f"Tool call {call_id} should be present in output"

        # 4. Verify text chunks are present
        text_chunks = [
            c
            for c in output_chunks
            if hasattr(c.choices[0], "delta")
            and c.choices[0].delta.get("content")
            and "Result" in c.choices[0].delta.get("content", "")
        ]
        assert len(text_chunks) == 2, f"Expected 2 text chunks, got {len(text_chunks)}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
