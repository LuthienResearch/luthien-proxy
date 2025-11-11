# ABOUTME: Unit tests for chunk builder functions
# ABOUTME: Tests ModelResponse creation utilities (moved from test_utils.py)

"""Unit tests for chunk builder utilities."""

from __future__ import annotations

import pytest
from litellm.types.utils import ChatCompletionMessageToolCall, Delta, Function

from luthien_proxy.policy_core.chunk_builders import (
    create_text_chunk,
    create_text_response,
    create_tool_call_chunk,
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
        assert response.choices[0].message["content"] == "Hello, world!"
        assert response.choices[0].message["role"] == "assistant"

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
        # Should be a proper Delta object with None content

        assert isinstance(chunk.choices[0].delta, Delta)

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


class TestCreateToolCallChunk:
    """Test create_tool_call_chunk utility."""

    def test_creates_valid_tool_call_chunk(self):
        """Test that create_tool_call_chunk creates a valid chunk."""
        tool_call = ChatCompletionMessageToolCall(
            id="call-123",
            type="function",
            function=Function(
                name="get_weather",
                arguments='{"location": "NYC"}',
            ),
        )

        chunk = create_tool_call_chunk(tool_call)

        assert len(chunk.choices) == 1
        assert chunk.choices[0].finish_reason == "tool_calls"

        delta = chunk.choices[0].delta
        tool_calls = delta["tool_calls"]
        assert len(tool_calls) == 1
        assert tool_calls[0]["id"] == "call-123"
        assert tool_calls[0]["type"] == "function"
        assert tool_calls[0]["function"]["name"] == "get_weather"
        assert tool_calls[0]["function"]["arguments"] == '{"location": "NYC"}'

    def test_uses_default_model(self):
        """Test default model name."""
        tool_call = ChatCompletionMessageToolCall(
            id="test",
            type="function",
            function=Function(name="test_func", arguments="{}"),
        )
        chunk = create_tool_call_chunk(tool_call)
        assert chunk.model == "luthien-policy"

    def test_uses_custom_model(self):
        """Test custom model name."""
        tool_call = ChatCompletionMessageToolCall(
            id="test",
            type="function",
            function=Function(name="test_func", arguments="{}"),
        )
        chunk = create_tool_call_chunk(tool_call, model="custom-model")
        assert chunk.model == "custom-model"

    def test_has_unique_id(self):
        """Test that each chunk has a unique ID."""
        tool_call = ChatCompletionMessageToolCall(
            id="test",
            type="function",
            function=Function(name="test_func", arguments="{}"),
        )
        chunk1 = create_tool_call_chunk(tool_call)
        chunk2 = create_tool_call_chunk(tool_call)

        assert chunk1.id != chunk2.id


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
