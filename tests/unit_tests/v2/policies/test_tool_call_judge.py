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
    """Test tool call detection in chunks."""

    def test_chunk_contains_tool_call_in_delta(self, judge_policy):
        """Test detecting tool call in delta."""
        chunk = {"choices": [{"delta": {"tool_calls": [{"id": "call-123", "function": {"name": "test"}}]}}]}

        assert judge_policy._chunk_contains_tool_call(chunk) is True

    def test_chunk_contains_tool_call_in_message(self, judge_policy):
        """Test detecting tool call in message."""
        chunk = {"choices": [{"message": {"tool_calls": [{"id": "call-123", "function": {"name": "test"}}]}}]}

        assert judge_policy._chunk_contains_tool_call(chunk) is True

    def test_chunk_without_tool_call(self, judge_policy):
        """Test chunk without tool calls returns False."""
        chunk = {"choices": [{"delta": {"content": "Hello"}}]}

        assert judge_policy._chunk_contains_tool_call(chunk) is False

    def test_is_tool_call_complete_with_finish_reason(self, judge_policy):
        """Test detecting complete tool call via finish_reason."""
        chunk = {"choices": [{"finish_reason": "tool_calls"}]}

        assert judge_policy._is_tool_call_complete(chunk) is True

    def test_is_tool_call_complete_with_message(self, judge_policy):
        """Test detecting complete tool call via message."""
        chunk = {"choices": [{"message": {"tool_calls": [{"id": "call-123", "function": {"name": "test"}}]}}]}

        assert judge_policy._is_tool_call_complete(chunk) is True

    def test_is_tool_call_not_complete(self, judge_policy):
        """Test incomplete tool call returns False."""
        chunk = {"choices": [{"delta": {"tool_calls": [{"index": 0}]}}]}

        assert judge_policy._is_tool_call_complete(chunk) is False


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
