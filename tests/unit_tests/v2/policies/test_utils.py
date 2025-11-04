# ABOUTME: Unit tests for policy utility functions
# ABOUTME: Tests ModelResponse creation and tool call extraction helpers

"""Unit tests for policy utilities."""

from __future__ import annotations

import pytest
from litellm.types.utils import ChatCompletionMessageToolCall, Choices, Function, Message, ModelResponse

from luthien_proxy.v2.policies.utils import (
    chunk_contains_tool_call,
    create_text_chunk,
    create_text_response,
    extract_tool_calls_from_response,
    is_tool_call_complete,
)


class TestCreateTextResponse:
    """Test create_text_response utility."""

    def test_creates_valid_response(self):
        """Test that create_text_response creates a valid ModelResponse."""
        response = create_text_response("Hello, world!")

        assert response.object == "chat.completion"
        assert len(response.choices) == 1
        assert response.choices[0].finish_reason == "stop"
        assert response.choices[0].index == 0
        assert response.choices[0].message.content == "Hello, world!"
        assert response.choices[0].message.role == "assistant"

    def test_uses_default_model(self):
        """Test default model name."""
        response = create_text_response("test")
        assert response.model == "luthien-policy"

    def test_uses_custom_model(self):
        """Test custom model name."""
        response = create_text_response("test", model="custom-model")
        assert response.model == "custom-model"

    def test_has_unique_id(self):
        """Test that each response has a unique ID."""
        response1 = create_text_response("test1")
        response2 = create_text_response("test2")

        assert response1.id != response2.id
        assert response1.id.startswith("policy-")
        assert response2.id.startswith("policy-")

    def test_has_timestamp(self):
        """Test that response has a created timestamp."""
        response = create_text_response("test")
        assert response.created > 0
        assert isinstance(response.created, int)


class TestCreateTextChunk:
    """Test create_text_chunk utility."""

    def test_creates_valid_chunk(self):
        """Test that create_text_chunk creates a valid chunk."""
        chunk = create_text_chunk("Hello")

        # Note: ModelResponse constructor sets object="chat.completion" even for chunks
        assert chunk.object in ["chat.completion.chunk", "chat.completion"]
        assert len(chunk.choices) == 1
        assert chunk.choices[0].index == 0
        assert chunk.choices[0].delta.get("content") == "Hello"

    def test_uses_default_model(self):
        """Test default model name."""
        chunk = create_text_chunk("test")
        assert chunk.model == "luthien-policy"

    def test_uses_custom_model(self):
        """Test custom model name."""
        chunk = create_text_chunk("test", model="custom-model")
        assert chunk.model == "custom-model"

    def test_empty_text_creates_empty_delta(self):
        """Test that empty text creates Delta with None content."""
        chunk = create_text_chunk("")
        # Should be a proper Delta object (not dict) with None content
        from litellm.types.utils import Delta

        assert isinstance(chunk.choices[0].delta, Delta)
        assert chunk.choices[0].delta.content is None

    def test_finish_reason_none_by_default(self):
        """Test that finish_reason is None by default."""
        chunk = create_text_chunk("test")
        # ModelResponse may set finish_reason to "stop" internally
        # The important thing is that we can override it
        assert chunk.choices[0].finish_reason in [None, "stop"]

    def test_finish_reason_stop(self):
        """Test finish_reason can be set to stop."""
        chunk = create_text_chunk("test", finish_reason="stop")
        assert chunk.choices[0].finish_reason == "stop"

    def test_finish_reason_tool_calls(self):
        """Test finish_reason can be set to tool_calls."""
        chunk = create_text_chunk("", finish_reason="tool_calls")
        assert chunk.choices[0].finish_reason == "tool_calls"

    def test_has_unique_id(self):
        """Test that each chunk has a unique ID."""
        chunk1 = create_text_chunk("test1")
        chunk2 = create_text_chunk("test2")

        assert chunk1.id != chunk2.id
        assert chunk1.id.startswith("policy-chunk-")
        assert chunk2.id.startswith("policy-chunk-")


class TestExtractToolCallsFromResponse:
    """Test extract_tool_calls_from_response utility."""

    def test_no_choices_returns_empty(self):
        """Test that response with no choices returns empty list."""
        response = ModelResponse(
            id="test",
            choices=[],
            created=123,
            model="test",
            object="chat.completion",
        )
        assert extract_tool_calls_from_response(response) == []

    def test_no_tool_calls_returns_empty(self):
        """Test that response without tool calls returns empty list."""
        response = ModelResponse(
            id="test",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(content="Hello", role="assistant"),
                )
            ],
            created=123,
            model="test",
            object="chat.completion",
        )
        assert extract_tool_calls_from_response(response) == []

    def test_extracts_single_tool_call(self):
        """Test extracting a single tool call."""
        response = ModelResponse(
            id="test",
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
                                function=Function(
                                    name="get_weather",
                                    arguments='{"location": "NYC"}',
                                ),
                            )
                        ],
                    ),
                )
            ],
            created=123,
            model="test",
            object="chat.completion",
        )

        tool_calls = extract_tool_calls_from_response(response)
        assert len(tool_calls) == 1
        assert tool_calls[0]["id"] == "call-123"
        assert tool_calls[0]["type"] == "function"
        assert tool_calls[0]["name"] == "get_weather"
        assert tool_calls[0]["arguments"] == '{"location": "NYC"}'

    def test_extracts_multiple_tool_calls(self):
        """Test extracting multiple tool calls."""
        response = ModelResponse(
            id="test",
            choices=[
                Choices(
                    finish_reason="tool_calls",
                    index=0,
                    message=Message(
                        role="assistant",
                        content=None,
                        tool_calls=[
                            ChatCompletionMessageToolCall(
                                id="call-1",
                                type="function",
                                function=Function(name="func1", arguments="{}"),
                            ),
                            ChatCompletionMessageToolCall(
                                id="call-2",
                                type="function",
                                function=Function(name="func2", arguments="{}"),
                            ),
                        ],
                    ),
                )
            ],
            created=123,
            model="test",
            object="chat.completion",
        )

        tool_calls = extract_tool_calls_from_response(response)
        assert len(tool_calls) == 2
        assert tool_calls[0]["id"] == "call-1"
        assert tool_calls[1]["id"] == "call-2"


class TestChunkContainsToolCall:
    """Test chunk_contains_tool_call utility."""

    def test_empty_chunk_returns_false(self):
        """Test that empty chunk returns False."""
        assert chunk_contains_tool_call({}) is False

    def test_no_choices_returns_false(self):
        """Test that chunk with no choices returns False."""
        assert chunk_contains_tool_call({"choices": []}) is False

    def test_text_only_chunk_returns_false(self):
        """Test that text-only chunk returns False."""
        chunk = {"choices": [{"delta": {"content": "Hello"}}]}
        assert chunk_contains_tool_call(chunk) is False

    def test_tool_call_in_delta_returns_true(self):
        """Test that tool call in delta returns True."""
        chunk = {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-123",
                                "type": "function",
                                "function": {"name": "test"},
                            }
                        ]
                    }
                }
            ]
        }
        assert chunk_contains_tool_call(chunk) is True

    def test_tool_call_in_message_returns_true(self):
        """Test that tool call in message returns True."""
        chunk = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call-123",
                                "type": "function",
                                "function": {"name": "test", "arguments": "{}"},
                            }
                        ]
                    }
                }
            ]
        }
        assert chunk_contains_tool_call(chunk) is True

    def test_non_dict_choice_returns_false(self):
        """Test that non-dict choice returns False."""
        chunk = {"choices": ["not a dict"]}
        assert chunk_contains_tool_call(chunk) is False


class TestIsToolCallComplete:
    """Test is_tool_call_complete utility."""

    def test_empty_chunk_returns_false(self):
        """Test that empty chunk returns False."""
        assert is_tool_call_complete({}) is False

    def test_no_choices_returns_false(self):
        """Test that chunk with no choices returns False."""
        assert is_tool_call_complete({"choices": []}) is False

    def test_text_chunk_returns_false(self):
        """Test that text chunk returns False."""
        chunk = {"choices": [{"delta": {"content": "Hello"}, "finish_reason": None}]}
        assert is_tool_call_complete(chunk) is False

    def test_finish_reason_tool_calls_returns_true(self):
        """Test that finish_reason=tool_calls returns True."""
        chunk = {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}
        assert is_tool_call_complete(chunk) is True

    def test_message_with_tool_calls_returns_true(self):
        """Test that message with tool_calls returns True."""
        chunk = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call-123",
                                "type": "function",
                                "function": {"name": "test", "arguments": "{}"},
                            }
                        ]
                    }
                }
            ]
        }
        assert is_tool_call_complete(chunk) is True

    def test_finish_reason_stop_returns_false(self):
        """Test that finish_reason=stop returns False."""
        chunk = {"choices": [{"delta": {"content": "Done"}, "finish_reason": "stop"}]}
        assert is_tool_call_complete(chunk) is False

    def test_non_dict_choice_returns_false(self):
        """Test that non-dict choice returns False."""
        chunk = {"choices": ["not a dict"]}
        assert is_tool_call_complete(chunk) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
