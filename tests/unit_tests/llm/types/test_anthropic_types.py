"""Tests for Anthropic type definitions."""

from luthien_proxy.llm.types.anthropic import (
    AnthropicContentBlock,
    AnthropicImageBlock,
    AnthropicImageSource,
    AnthropicImageSourceBase64,
    AnthropicImageSourceUrl,
    AnthropicMessage,
    AnthropicResponse,
    AnthropicTextBlock,
    AnthropicToolResultBlock,
    AnthropicToolUseBlock,
    AnthropicUsage,
)


class TestAnthropicImageTypes:
    """Test Anthropic image type definitions."""

    def test_image_source_base64(self):
        """Test AnthropicImageSourceBase64 TypedDict."""
        source: AnthropicImageSourceBase64 = {
            "type": "base64",
            "media_type": "image/png",
            "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ",
        }
        assert source["type"] == "base64"
        assert source["media_type"] == "image/png"
        assert "data" in source

    def test_image_source_url(self):
        """Test AnthropicImageSourceUrl TypedDict."""
        source: AnthropicImageSourceUrl = {"type": "url", "url": "https://example.com/image.png"}
        assert source["type"] == "url"
        assert source["url"] == "https://example.com/image.png"

    def test_image_source_union(self):
        """Test AnthropicImageSource union type."""
        base64_source: AnthropicImageSource = {"type": "base64", "media_type": "image/jpeg", "data": "abc123"}
        url_source: AnthropicImageSource = {"type": "url", "url": "https://example.com"}

        assert base64_source["type"] == "base64"
        assert url_source["type"] == "url"

    def test_image_block(self):
        """Test AnthropicImageBlock TypedDict."""
        block: AnthropicImageBlock = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "base64data"},
        }
        assert block["type"] == "image"
        assert block["source"]["type"] == "base64"


class TestAnthropicContentBlocks:
    """Test Anthropic content block type definitions."""

    def test_text_block(self):
        """Test AnthropicTextBlock TypedDict."""
        block: AnthropicTextBlock = {"type": "text", "text": "Hello, how can I help?"}
        assert block["type"] == "text"
        assert block["text"] == "Hello, how can I help?"

    def test_tool_use_block(self):
        """Test AnthropicToolUseBlock TypedDict."""
        block: AnthropicToolUseBlock = {
            "type": "tool_use",
            "id": "toolu_01ABC",
            "name": "get_weather",
            "input": {"location": "San Francisco"},
        }
        assert block["type"] == "tool_use"
        assert block["id"] == "toolu_01ABC"
        assert block["name"] == "get_weather"
        assert block["input"]["location"] == "San Francisco"

    def test_tool_result_block(self):
        """Test AnthropicToolResultBlock TypedDict."""
        block: AnthropicToolResultBlock = {
            "type": "tool_result",
            "tool_use_id": "toolu_01ABC",
            "content": "Sunny, 72°F",
        }
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "toolu_01ABC"
        assert block["content"] == "Sunny, 72°F"

    def test_tool_result_block_with_error(self):
        """Test AnthropicToolResultBlock with is_error flag."""
        block: AnthropicToolResultBlock = {
            "type": "tool_result",
            "tool_use_id": "toolu_01ABC",
            "content": "Error: Invalid location",
            "is_error": True,
        }
        assert block["is_error"] is True

    def test_content_block_union(self):
        """Test AnthropicContentBlock union accepts all block types."""
        blocks: list[AnthropicContentBlock] = [
            {"type": "text", "text": "Hello"},
            {"type": "image", "source": {"type": "url", "url": "http://example.com"}},
            {"type": "tool_use", "id": "123", "name": "test", "input": {}},
            {"type": "tool_result", "tool_use_id": "123", "content": "result"},
        ]
        assert len(blocks) == 4


class TestAnthropicMessageTypes:
    """Test Anthropic message and response type definitions."""

    def test_message_string_content(self):
        """Test AnthropicMessage with string content."""
        msg: AnthropicMessage = {"role": "user", "content": "Hello!"}
        assert msg["role"] == "user"
        assert msg["content"] == "Hello!"

    def test_message_block_content(self):
        """Test AnthropicMessage with content blocks."""
        msg: AnthropicMessage = {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I'll help you."},
                {"type": "tool_use", "id": "123", "name": "search", "input": {"q": "test"}},
            ],
        }
        assert msg["role"] == "assistant"
        assert len(msg["content"]) == 2

    def test_usage(self):
        """Test AnthropicUsage TypedDict."""
        usage: AnthropicUsage = {"input_tokens": 100, "output_tokens": 50}
        assert usage["input_tokens"] == 100
        assert usage["output_tokens"] == 50

    def test_response(self):
        """Test AnthropicResponse TypedDict."""
        response: AnthropicResponse = {
            "id": "msg_01ABC",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello!"}],
            "model": "claude-3-5-sonnet-20241022",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "stop_reason": "end_turn",
        }
        assert response["id"] == "msg_01ABC"
        assert response["type"] == "message"
        assert response["role"] == "assistant"
        assert len(response["content"]) == 1
        assert response["model"] == "claude-3-5-sonnet-20241022"
        assert response["stop_reason"] == "end_turn"

    def test_response_without_stop_reason(self):
        """Test AnthropicResponse without optional stop_reason."""
        response: AnthropicResponse = {
            "id": "msg_02XYZ",
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": "claude-3-opus",
            "usage": {"input_tokens": 5, "output_tokens": 0},
        }
        # stop_reason is optional
        assert "stop_reason" not in response
