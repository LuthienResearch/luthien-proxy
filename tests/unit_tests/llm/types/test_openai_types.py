"""Tests for OpenAI type definitions."""

import pytest
from pydantic import ValidationError

from luthien_proxy.llm.types.openai import (
    AssistantMessage,
    ContentPart,
    FunctionCall,
    ImageContentPart,
    ImageUrl,
    Message,
    Request,
    SystemMessage,
    TextContentPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)


class TestContentPartTypes:
    """Test content part type definitions."""

    def test_text_content_part(self):
        """Test TextContentPart TypedDict."""
        part: TextContentPart = {"type": "text", "text": "Hello world"}
        assert part["type"] == "text"
        assert part["text"] == "Hello world"

    def test_image_content_part(self):
        """Test ImageContentPart TypedDict."""
        image_url: ImageUrl = {"url": "https://example.com/image.png"}
        part: ImageContentPart = {"type": "image_url", "image_url": image_url}
        assert part["type"] == "image_url"
        assert part["image_url"]["url"] == "https://example.com/image.png"

    def test_image_url_with_detail(self):
        """Test ImageUrl with optional detail field."""
        image_url: ImageUrl = {"url": "https://example.com/image.png", "detail": "high"}
        assert image_url["detail"] == "high"

    def test_content_part_union(self):
        """Test ContentPart union type accepts both text and image parts."""
        text_part: ContentPart = {"type": "text", "text": "Hello"}
        image_part: ContentPart = {"type": "image_url", "image_url": {"url": "http://example.com"}}

        # Both should be valid ContentPart types
        assert text_part["type"] == "text"
        assert image_part["type"] == "image_url"


class TestMessageTypes:
    """Test message type definitions."""

    def test_system_message(self):
        """Test SystemMessage TypedDict."""
        msg: SystemMessage = {"role": "system", "content": "You are a helpful assistant"}
        assert msg["role"] == "system"
        assert msg["content"] == "You are a helpful assistant"

    def test_user_message_string_content(self):
        """Test UserMessage with string content."""
        msg: UserMessage = {"role": "user", "content": "Hello!"}
        assert msg["role"] == "user"
        assert msg["content"] == "Hello!"

    def test_user_message_multimodal_content(self):
        """Test UserMessage with multimodal content."""
        content: list[ContentPart] = [
            {"type": "text", "text": "What is this?"},
            {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
        ]
        msg: UserMessage = {"role": "user", "content": content}
        assert msg["role"] == "user"
        assert len(msg["content"]) == 2

    def test_assistant_message_with_content(self):
        """Test AssistantMessage with text content."""
        msg: AssistantMessage = {"role": "assistant", "content": "I can help with that!"}
        assert msg["role"] == "assistant"
        assert msg["content"] == "I can help with that!"

    def test_assistant_message_with_tool_calls(self):
        """Test AssistantMessage with tool_calls."""
        tool_call: ToolCall = {
            "id": "call_123",
            "type": "function",
            "function": {"name": "get_weather", "arguments": '{"location": "NYC"}'},
        }
        msg: AssistantMessage = {"role": "assistant", "content": None, "tool_calls": [tool_call]}
        assert msg["role"] == "assistant"
        assert msg["content"] is None
        assert len(msg["tool_calls"]) == 1
        assert msg["tool_calls"][0]["function"]["name"] == "get_weather"

    def test_tool_message(self):
        """Test ToolMessage TypedDict."""
        msg: ToolMessage = {"role": "tool", "content": "Weather: Sunny, 72°F", "tool_call_id": "call_123"}
        assert msg["role"] == "tool"
        assert msg["content"] == "Weather: Sunny, 72°F"
        assert msg["tool_call_id"] == "call_123"

    def test_message_union(self):
        """Test Message union accepts all message types."""
        messages: list[Message] = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
            {"role": "tool", "content": "Result", "tool_call_id": "123"},
        ]
        assert len(messages) == 4


class TestFunctionAndToolTypes:
    """Test function and tool call type definitions."""

    def test_function_call(self):
        """Test FunctionCall TypedDict."""
        func: FunctionCall = {"name": "search", "arguments": '{"query": "python"}'}
        assert func["name"] == "search"
        assert func["arguments"] == '{"query": "python"}'

    def test_tool_call(self):
        """Test ToolCall TypedDict."""
        tool: ToolCall = {
            "id": "call_abc",
            "type": "function",
            "function": {"name": "calculator", "arguments": '{"expression": "2+2"}'},
        }
        assert tool["id"] == "call_abc"
        assert tool["type"] == "function"
        assert tool["function"]["name"] == "calculator"


class TestRequestModel:
    """Test Request Pydantic model."""

    def test_basic_request(self):
        """Test creating a basic Request."""
        req = Request(model="gpt-4", messages=[{"role": "user", "content": "Hello"}])
        assert req.model == "gpt-4"
        assert len(req.messages) == 1
        assert req.stream is False

    def test_request_with_all_fields(self):
        """Test Request with all optional fields."""
        req = Request(
            model="gpt-4",
            messages=[{"role": "user", "content": "Test"}],
            max_tokens=100,
            temperature=0.7,
            stream=True,
        )
        assert req.max_tokens == 100
        assert req.temperature == 0.7
        assert req.stream is True

    def test_request_extra_fields(self):
        """Test Request accepts extra fields."""
        req = Request(model="gpt-4", messages=[], custom_param="value")
        assert req.model_extra["custom_param"] == "value"

    def test_request_validation(self):
        """Test Request validation."""
        with pytest.raises(ValidationError):
            Request(messages=[])  # missing model

    def test_last_message_string(self):
        """Test last_message with string content."""
        req = Request(model="gpt-4", messages=[{"role": "user", "content": "Hello"}])
        assert req.last_message == "Hello"

    def test_last_message_multimodal(self):
        """Test last_message with multimodal content."""
        req = Request(
            model="gpt-4",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is this?"},
                        {"type": "image_url", "image_url": {"url": "http://example.com"}},
                    ],
                }
            ],
        )
        assert req.last_message == "What is this?"

    def test_last_message_empty(self):
        """Test last_message with no messages."""
        req = Request(model="gpt-4", messages=[])
        assert req.last_message == ""

    def test_last_message_none_content(self):
        """Test last_message with None content."""
        req = Request(model="gpt-4", messages=[{"role": "assistant", "content": None}])
        assert req.last_message == ""
