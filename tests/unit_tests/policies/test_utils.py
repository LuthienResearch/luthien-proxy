"""Unit tests for policy utilities."""

from __future__ import annotations

import pytest
from litellm.types.utils import ChatCompletionMessageToolCall, Choices, Function, Message, ModelResponse

from luthien_proxy.policy_core.response_utils import (
    extract_tool_calls_from_response,
)


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
