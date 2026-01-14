# ABOUTME: Unit tests for LLM format conversion functions
# ABOUTME: Tests OpenAI/Anthropic bidirectional format transformations

"""Tests for LLM format converters."""

from litellm.types.utils import Choices, Message, ModelResponse, Usage

from luthien_proxy.llm.llm_format_utils import (
    anthropic_to_openai_request,
    openai_to_anthropic_response,
)


class TestAnthropicToOpenAIRequest:
    """Test Anthropic to OpenAI request conversion."""

    def test_minimal_conversion(self):
        """Test converting minimal Anthropic request."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
        }

        result = anthropic_to_openai_request(anthropic_req)

        assert result["model"] == "claude-3-opus-20240229"
        assert result["messages"] == [{"role": "user", "content": "Hello"}]
        assert result["max_tokens"] == 1024
        assert result["stream"] is False

    def test_thinking_parameter_preserved(self):
        """Test that thinking parameter passes through to output."""
        anthropic_req = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Think about this"}],
            "max_tokens": 16000,
            "thinking": {"type": "enabled", "budget_tokens": 10000},
        }

        result = anthropic_to_openai_request(anthropic_req)

        assert result["thinking"] == {"type": "enabled", "budget_tokens": 10000}

    def test_metadata_parameter_preserved(self):
        """Test that metadata parameter passes through."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
            "metadata": {"user_id": "user_123"},
        }

        result = anthropic_to_openai_request(anthropic_req)

        assert result["metadata"] == {"user_id": "user_123"}

    def test_stop_sequences_mapped_to_stop(self):
        """Test that stop_sequences is mapped to 'stop' (OpenAI format)."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
            "stop_sequences": ["END", "STOP"],
        }

        result = anthropic_to_openai_request(anthropic_req)

        # Anthropic's stop_sequences should be mapped to OpenAI's stop
        assert result["stop"] == ["END", "STOP"]
        assert "stop_sequences" not in result

    def test_tool_choice_auto_converted(self):
        """Test that Anthropic tool_choice auto is converted to OpenAI format."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
            "tool_choice": {"type": "auto"},
        }

        result = anthropic_to_openai_request(anthropic_req)

        # Anthropic {"type": "auto"} -> OpenAI "auto" (string)
        assert result["tool_choice"] == "auto"

    def test_tool_choice_any_converted_to_required(self):
        """Test that Anthropic tool_choice any is converted to OpenAI required."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
            "tool_choice": {"type": "any"},
        }

        result = anthropic_to_openai_request(anthropic_req)

        # Anthropic {"type": "any"} (force tool use) -> OpenAI "required"
        assert result["tool_choice"] == "required"

    def test_tool_choice_specific_tool_converted(self):
        """Test that Anthropic specific tool_choice is converted to OpenAI format."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
            "tool_choice": {"type": "tool", "name": "get_weather"},
        }

        result = anthropic_to_openai_request(anthropic_req)

        # Anthropic {"type": "tool", "name": X} -> OpenAI {"type": "function", "function": {"name": X}}
        assert result["tool_choice"] == {"type": "function", "function": {"name": "get_weather"}}

    def test_tool_choice_openai_format_passthrough(self):
        """Test that OpenAI-format tool_choice passes through unchanged."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
            "tool_choice": "none",  # Already OpenAI format
        }

        result = anthropic_to_openai_request(anthropic_req)

        assert result["tool_choice"] == "none"

    def test_multiple_extra_params_preserved(self):
        """Test that multiple extra parameters all pass through."""
        anthropic_req = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 16000,
            "thinking": {"type": "enabled", "budget_tokens": 10000},
            "metadata": {"user_id": "user_123"},
            "stop_sequences": ["END"],
            "custom_param": "custom_value",
        }

        result = anthropic_to_openai_request(anthropic_req)

        assert result["thinking"] == {"type": "enabled", "budget_tokens": 10000}
        assert result["metadata"] == {"user_id": "user_123"}
        # stop_sequences is mapped to stop (OpenAI format)
        assert result["stop"] == ["END"]
        assert "stop_sequences" not in result
        assert result["custom_param"] == "custom_value"

    def test_none_extra_params_filtered(self):
        """Test that None extra parameters are filtered out."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
            "thinking": None,
            "metadata": None,
        }

        result = anthropic_to_openai_request(anthropic_req)

        assert "thinking" not in result
        assert "metadata" not in result

    def test_with_optional_params(self):
        """Test conversion with temperature and top_p."""
        anthropic_req = {
            "model": "claude-3-sonnet-20240229",
            "messages": [{"role": "user", "content": "Test"}],
            "max_tokens": 500,
            "temperature": 0.7,
            "top_p": 0.9,
        }

        result = anthropic_to_openai_request(anthropic_req)

        assert result["temperature"] == 0.7
        assert result["top_p"] == 0.9

    def test_with_streaming(self):
        """Test conversion with streaming enabled."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [{"role": "user", "content": "Stream this"}],
            "max_tokens": 1024,
            "stream": True,
        }

        result = anthropic_to_openai_request(anthropic_req)

        assert result["stream"] is True

    def test_system_parameter_conversion(self):
        """Test that Anthropic system parameter becomes first message."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
            "system": "You are a helpful assistant.",
        }

        result = anthropic_to_openai_request(anthropic_req)

        assert len(result["messages"]) == 2
        assert result["messages"][0]["role"] == "system"
        assert result["messages"][0]["content"] == "You are a helpful assistant."
        assert result["messages"][1]["role"] == "user"

    def test_filters_none_values(self):
        """Test that None values are filtered out."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
            "temperature": None,
        }

        result = anthropic_to_openai_request(anthropic_req)

        assert "temperature" not in result


class TestOpenAIToAnthropicResponseThinkingBlocks:
    """Test thinking blocks handling in OpenAI to Anthropic response conversion."""

    def test_thinking_blocks_appear_first_in_content(self):
        """Test that thinking blocks are placed first in content array.

        When thinking is enabled, Anthropic API requires thinking blocks
        to appear BEFORE text content. LiteLLM exposes these via
        message.thinking_blocks.
        """
        # Create a mock message with thinking_blocks attribute
        message = Message(role="assistant", content="Here is my response.")
        # LiteLLM adds thinking_blocks as an attribute
        message.thinking_blocks = [
            {
                "type": "thinking",
                "thinking": "Let me think about this step by step...",
                "signature": "sig_abc123",
            }
        ]

        openai_response = ModelResponse(
            id="test-id-thinking",
            created=1234567890,
            model="claude-sonnet-4-20250514",
            object="chat.completion",
            choices=[
                Choices(
                    index=0,
                    message=message,
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=100, completion_tokens=500, total_tokens=600),
        )

        result = openai_to_anthropic_response(openai_response)

        # Thinking block should be FIRST, then text
        assert len(result["content"]) == 2
        assert result["content"][0]["type"] == "thinking"
        assert result["content"][0]["thinking"] == "Let me think about this step by step..."
        assert result["content"][0]["signature"] == "sig_abc123"
        assert result["content"][1]["type"] == "text"
        assert result["content"][1]["text"] == "Here is my response."

    def test_multiple_thinking_blocks(self):
        """Test handling multiple thinking blocks in sequence."""
        message = Message(role="assistant", content="Final answer.")
        message.thinking_blocks = [
            {"type": "thinking", "thinking": "First thought", "signature": "sig_1"},
            {"type": "thinking", "thinking": "Second thought", "signature": "sig_2"},
        ]

        openai_response = ModelResponse(
            id="test-id",
            created=1234567890,
            model="claude-sonnet-4-20250514",
            object="chat.completion",
            choices=[Choices(index=0, message=message, finish_reason="stop")],
            usage=Usage(prompt_tokens=50, completion_tokens=200, total_tokens=250),
        )

        result = openai_to_anthropic_response(openai_response)

        assert len(result["content"]) == 3
        assert result["content"][0]["type"] == "thinking"
        assert result["content"][0]["thinking"] == "First thought"
        assert result["content"][1]["type"] == "thinking"
        assert result["content"][1]["thinking"] == "Second thought"
        assert result["content"][2]["type"] == "text"

    def test_thinking_blocks_with_tool_calls(self):
        """Test thinking blocks ordering when tool calls are also present.

        Order should be: thinking -> text -> tool_use (per Anthropic spec).
        """
        from litellm.types.utils import ChatCompletionMessageToolCall, Function

        message = Message(
            role="assistant",
            content="Let me use a tool.",
            tool_calls=[
                ChatCompletionMessageToolCall(
                    id="call_123",
                    type="function",
                    function=Function(name="get_data", arguments="{}"),
                )
            ],
        )
        message.thinking_blocks = [
            {"type": "thinking", "thinking": "I should use the tool", "signature": "sig_x"},
        ]

        openai_response = ModelResponse(
            id="test-id",
            created=1234567890,
            model="claude-sonnet-4-20250514",
            object="chat.completion",
            choices=[Choices(index=0, message=message, finish_reason="tool_calls")],
            usage=Usage(prompt_tokens=50, completion_tokens=100, total_tokens=150),
        )

        result = openai_to_anthropic_response(openai_response)

        # Order: thinking -> text -> tool_use
        assert len(result["content"]) == 3
        assert result["content"][0]["type"] == "thinking"
        assert result["content"][1]["type"] == "text"
        assert result["content"][2]["type"] == "tool_use"

    def test_empty_thinking_blocks_list(self):
        """Test that empty thinking_blocks list is handled like no blocks."""
        message = Message(role="assistant", content="Response with empty list.")
        message.thinking_blocks = []  # Empty list, not None

        openai_response = ModelResponse(
            id="test-id",
            created=1234567890,
            model="claude-sonnet-4-20250514",
            object="chat.completion",
            choices=[Choices(index=0, message=message, finish_reason="stop")],
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

        result = openai_to_anthropic_response(openai_response)

        # Empty list should be treated as no thinking blocks
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"

    def test_redacted_thinking_block(self):
        """Test handling of redacted_thinking blocks.

        Redacted thinking blocks have a different structure:
        - type: "redacted_thinking"
        - data: encrypted/redacted content (instead of thinking + signature)
        """
        message = Message(role="assistant", content="Response after redacted thinking.")
        message.thinking_blocks = [
            {"type": "redacted_thinking", "data": "encrypted_data_abc123"},
        ]

        openai_response = ModelResponse(
            id="test-id",
            created=1234567890,
            model="claude-sonnet-4-20250514",
            object="chat.completion",
            choices=[Choices(index=0, message=message, finish_reason="stop")],
            usage=Usage(prompt_tokens=50, completion_tokens=100, total_tokens=150),
        )

        result = openai_to_anthropic_response(openai_response)

        assert len(result["content"]) == 2
        assert result["content"][0]["type"] == "redacted_thinking"
        assert result["content"][0]["data"] == "encrypted_data_abc123"
        assert "thinking" not in result["content"][0]  # Should not have thinking field
        assert result["content"][1]["type"] == "text"

    def test_thinking_blocks_without_text_content(self):
        """Test response with thinking blocks but no text content.

        Edge case where model outputs only thinking (e.g., internal reasoning
        that doesn't produce visible output).
        """
        message = Message(role="assistant", content=None)
        message.thinking_blocks = [
            {"type": "thinking", "thinking": "Internal reasoning...", "signature": "sig"},
        ]

        openai_response = ModelResponse(
            id="test-id",
            created=1234567890,
            model="claude-sonnet-4-20250514",
            object="chat.completion",
            choices=[Choices(index=0, message=message, finish_reason="stop")],
            usage=Usage(prompt_tokens=50, completion_tokens=100, total_tokens=150),
        )

        result = openai_to_anthropic_response(openai_response)

        # Should have only thinking block, no text
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "thinking"

    def test_mixed_thinking_and_redacted_blocks(self):
        """Test response with both thinking and redacted_thinking blocks."""
        message = Message(role="assistant", content="Final response.")
        message.thinking_blocks = [
            {"type": "thinking", "thinking": "First thought", "signature": "sig1"},
            {"type": "redacted_thinking", "data": "redacted_content"},
            {"type": "thinking", "thinking": "Third thought", "signature": "sig3"},
        ]

        openai_response = ModelResponse(
            id="test-id",
            created=1234567890,
            model="claude-sonnet-4-20250514",
            object="chat.completion",
            choices=[Choices(index=0, message=message, finish_reason="stop")],
            usage=Usage(prompt_tokens=50, completion_tokens=200, total_tokens=250),
        )

        result = openai_to_anthropic_response(openai_response)

        assert len(result["content"]) == 4
        assert result["content"][0]["type"] == "thinking"
        assert result["content"][0]["thinking"] == "First thought"
        assert result["content"][1]["type"] == "redacted_thinking"
        assert result["content"][1]["data"] == "redacted_content"
        assert result["content"][2]["type"] == "thinking"
        assert result["content"][2]["thinking"] == "Third thought"
        assert result["content"][3]["type"] == "text"


class TestOpenAIToAnthropicResponse:
    """Test OpenAI to Anthropic response conversion."""

    def test_basic_response_conversion(self):
        """Test converting basic OpenAI response."""
        openai_response = ModelResponse(
            id="test-id-123",
            created=1234567890,
            model="gpt-4",
            object="chat.completion",
            choices=[
                Choices(
                    index=0,
                    message=Message(role="assistant", content="Hello there!"),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

        result = openai_to_anthropic_response(openai_response)

        assert result["id"] == "test-id-123"
        assert result["type"] == "message"
        assert result["role"] == "assistant"
        assert result["content"] == [{"type": "text", "text": "Hello there!"}]
        assert result["model"] == "gpt-4"
        assert result["usage"]["input_tokens"] == 10
        assert result["usage"]["output_tokens"] == 5

    def test_stop_reason_conversion(self):
        """Test finish_reason conversion to stop_reason."""
        openai_response = ModelResponse(
            id="test-id",
            created=1234567890,
            model="gpt-4",
            object="chat.completion",
            choices=[
                Choices(
                    index=0,
                    message=Message(role="assistant", content="Test"),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=5, completion_tokens=3, total_tokens=8),
        )

        result = openai_to_anthropic_response(openai_response)

        assert result["stop_reason"] == "end_turn"

    def test_non_stop_finish_reason(self):
        """Test finish reasons are properly mapped to Anthropic format."""
        openai_response = ModelResponse(
            id="test-id",
            created=1234567890,
            model="gpt-4",
            object="chat.completion",
            choices=[
                Choices(
                    index=0,
                    message=Message(role="assistant", content="Test"),
                    finish_reason="length",
                )
            ],
            usage=Usage(prompt_tokens=5, completion_tokens=100, total_tokens=105),
        )

        result = openai_to_anthropic_response(openai_response)

        # OpenAI's "length" should map to Anthropic's "max_tokens"
        assert result["stop_reason"] == "max_tokens"

    def test_tool_calls_finish_reason(self):
        """Test tool_calls finish reason maps to tool_use."""
        openai_response = ModelResponse(
            id="test-id",
            created=1234567890,
            model="gpt-4",
            object="chat.completion",
            choices=[
                Choices(
                    index=0,
                    message=Message(role="assistant", content="Test"),
                    finish_reason="tool_calls",
                )
            ],
            usage=Usage(prompt_tokens=5, completion_tokens=10, total_tokens=15),
        )

        result = openai_to_anthropic_response(openai_response)

        assert result["stop_reason"] == "tool_use"

    def test_unknown_finish_reason_passed_through(self):
        """Test unknown finish reasons are passed through unchanged."""
        openai_response = ModelResponse(
            id="test-id",
            created=1234567890,
            model="gpt-4",
            object="chat.completion",
            choices=[
                Choices(
                    index=0,
                    message=Message(role="assistant", content="Test"),
                    finish_reason="content_filter",
                )
            ],
            usage=Usage(prompt_tokens=5, completion_tokens=10, total_tokens=15),
        )

        result = openai_to_anthropic_response(openai_response)

        assert result["stop_reason"] == "content_filter"


class TestAnthropicToOpenAIRequestArrayContent:
    """Test Anthropic to OpenAI conversion for array content blocks."""

    def test_text_block_array(self):
        """Test converting array of text blocks."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Hello"},
                        {"type": "text", "text": "World"},
                    ],
                }
            ],
            "max_tokens": 1024,
        }

        result = anthropic_to_openai_request(anthropic_req)

        # Text parts should be joined with space
        assert result["messages"][0]["content"] == "Hello World"

    def test_image_base64_conversion(self):
        """Test converting base64 image from Anthropic to OpenAI format."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is in this image?"},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": "iVBORw0KGgoAAAANSUhEUgAAAAUA",
                            },
                        },
                    ],
                }
            ],
            "max_tokens": 1024,
        }

        result = anthropic_to_openai_request(anthropic_req)

        content = result["messages"][0]["content"]
        assert isinstance(content, list)
        assert len(content) == 2
        assert content[0] == {"type": "text", "text": "What is in this image?"}
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"] == "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAUA"

    def test_image_url_conversion(self):
        """Test converting URL image from Anthropic to OpenAI format."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "url",
                                "url": "https://example.com/image.png",
                            },
                        },
                    ],
                }
            ],
            "max_tokens": 1024,
        }

        result = anthropic_to_openai_request(anthropic_req)

        content = result["messages"][0]["content"]
        assert isinstance(content, list)
        assert len(content) == 1
        assert content[0]["type"] == "image_url"
        assert content[0]["image_url"]["url"] == "https://example.com/image.png"

    def test_image_default_media_type(self):
        """Test that base64 images default to image/png media type."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "data": "abc123",
                            },
                        },
                    ],
                }
            ],
            "max_tokens": 1024,
        }

        result = anthropic_to_openai_request(anthropic_req)

        content = result["messages"][0]["content"]
        assert content[0]["image_url"]["url"] == "data:image/png;base64,abc123"

    def test_image_only_no_text(self):
        """Test image-only message without text."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": "base64data",
                            },
                        },
                    ],
                }
            ],
            "max_tokens": 1024,
        }

        result = anthropic_to_openai_request(anthropic_req)

        content = result["messages"][0]["content"]
        assert isinstance(content, list)
        assert len(content) == 1
        assert content[0]["type"] == "image_url"

    def test_multiple_images(self):
        """Test message with multiple images."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Compare these:"},
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": "img1"},
                        },
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": "img2"},
                        },
                    ],
                }
            ],
            "max_tokens": 1024,
        }

        result = anthropic_to_openai_request(anthropic_req)

        content = result["messages"][0]["content"]
        assert len(content) == 3
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
        assert content[2]["type"] == "image_url"


class TestAnthropicToOpenAIRequestToolResults:
    """Test Anthropic to OpenAI conversion for tool results."""

    def test_tool_result_conversion(self):
        """Test converting tool_result blocks to OpenAI tool messages."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool_123",
                            "content": "The result of the tool call",
                        },
                    ],
                }
            ],
            "max_tokens": 1024,
        }

        result = anthropic_to_openai_request(anthropic_req)

        assert result["messages"][0]["role"] == "tool"
        assert result["messages"][0]["tool_call_id"] == "tool_123"
        assert result["messages"][0]["content"] == "The result of the tool call"

    def test_multiple_tool_results(self):
        """Test converting multiple tool_result blocks."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tool_1", "content": "Result 1"},
                        {"type": "tool_result", "tool_use_id": "tool_2", "content": "Result 2"},
                    ],
                }
            ],
            "max_tokens": 1024,
        }

        result = anthropic_to_openai_request(anthropic_req)

        assert len(result["messages"]) == 2
        assert result["messages"][0]["tool_call_id"] == "tool_1"
        assert result["messages"][1]["tool_call_id"] == "tool_2"

    def test_tool_result_with_text(self):
        """Test tool_result with accompanying text creates separate messages."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tool_1", "content": "Result"},
                        {"type": "text", "text": "Additional context"},
                    ],
                }
            ],
            "max_tokens": 1024,
        }

        result = anthropic_to_openai_request(anthropic_req)

        # Should have tool message and user message
        assert len(result["messages"]) == 2
        assert result["messages"][0]["role"] == "tool"
        assert result["messages"][1]["role"] == "user"
        assert result["messages"][1]["content"] == "Additional context"


class TestAnthropicToOpenAIRequestToolUse:
    """Test Anthropic to OpenAI conversion for tool_use blocks."""

    def test_tool_use_conversion(self):
        """Test converting assistant tool_use to OpenAI tool_calls."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_123",
                            "name": "get_weather",
                            "input": {"location": "San Francisco"},
                        },
                    ],
                }
            ],
            "max_tokens": 1024,
        }

        result = anthropic_to_openai_request(anthropic_req)

        msg = result["messages"][0]
        assert msg["role"] == "assistant"
        assert msg["content"] is None
        assert len(msg["tool_calls"]) == 1
        assert msg["tool_calls"][0]["id"] == "toolu_123"
        assert msg["tool_calls"][0]["type"] == "function"
        assert msg["tool_calls"][0]["function"]["name"] == "get_weather"
        assert msg["tool_calls"][0]["function"]["arguments"] == '{"location": "San Francisco"}'

    def test_tool_use_with_text(self):
        """Test tool_use with accompanying text."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Let me check the weather."},
                        {
                            "type": "tool_use",
                            "id": "toolu_456",
                            "name": "get_weather",
                            "input": {"city": "NYC"},
                        },
                    ],
                }
            ],
            "max_tokens": 1024,
        }

        result = anthropic_to_openai_request(anthropic_req)

        msg = result["messages"][0]
        assert msg["content"] == "Let me check the weather."
        assert len(msg["tool_calls"]) == 1

    def test_multiple_tool_uses(self):
        """Test converting multiple tool_use blocks."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "tool_a", "input": {}},
                        {"type": "tool_use", "id": "t2", "name": "tool_b", "input": {"x": 1}},
                    ],
                }
            ],
            "max_tokens": 1024,
        }

        result = anthropic_to_openai_request(anthropic_req)

        msg = result["messages"][0]
        assert len(msg["tool_calls"]) == 2
        assert msg["tool_calls"][0]["function"]["name"] == "tool_a"
        assert msg["tool_calls"][1]["function"]["name"] == "tool_b"


class TestAnthropicToOpenAIRequestTools:
    """Test Anthropic to OpenAI tools definition conversion."""

    def test_tools_conversion(self):
        """Test converting Anthropic tools to OpenAI format."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [{"role": "user", "content": "Use tools"}],
            "max_tokens": 1024,
            "tools": [
                {
                    "name": "get_weather",
                    "description": "Get current weather for a location",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string", "description": "City name"},
                        },
                        "required": ["location"],
                    },
                },
            ],
        }

        result = anthropic_to_openai_request(anthropic_req)

        assert "tools" in result
        assert len(result["tools"]) == 1
        tool = result["tools"][0]
        assert tool["type"] == "function"
        assert tool["function"]["name"] == "get_weather"
        assert tool["function"]["description"] == "Get current weather for a location"
        assert tool["function"]["parameters"]["type"] == "object"
        assert "location" in tool["function"]["parameters"]["properties"]

    def test_multiple_tools(self):
        """Test converting multiple tools."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [{"role": "user", "content": "Use tools"}],
            "max_tokens": 1024,
            "tools": [
                {"name": "tool_a", "description": "Tool A", "input_schema": {}},
                {"name": "tool_b", "description": "Tool B", "input_schema": {}},
            ],
        }

        result = anthropic_to_openai_request(anthropic_req)

        assert len(result["tools"]) == 2
        assert result["tools"][0]["function"]["name"] == "tool_a"
        assert result["tools"][1]["function"]["name"] == "tool_b"


class TestAnthropicToOpenAIRequestSystem:
    """Test Anthropic to OpenAI system parameter conversion."""

    def test_system_as_content_blocks(self):
        """Test system parameter as array of content blocks."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
            "system": [
                {"type": "text", "text": "You are helpful."},
                {"type": "text", "text": "Be concise."},
            ],
        }

        result = anthropic_to_openai_request(anthropic_req)

        assert result["messages"][0]["role"] == "system"
        assert result["messages"][0]["content"] == "You are helpful. Be concise."

    def test_system_empty_blocks(self):
        """Test system with empty content blocks."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
            "system": [],
        }

        result = anthropic_to_openai_request(anthropic_req)

        assert result["messages"][0]["role"] == "system"
        assert result["messages"][0]["content"] == ""


class TestAnthropicToOpenAIRequestEdgeCases:
    """Test edge cases in Anthropic to OpenAI conversion."""

    def test_unknown_block_type(self):
        """Test handling unknown content block types."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "unknown_type", "data": "something"},
                    ],
                }
            ],
            "max_tokens": 1024,
        }

        result = anthropic_to_openai_request(anthropic_req)

        # Should create error message with unknown types
        assert "unknown_type" in result["messages"][0]["content"]

    def test_non_dict_block_in_array(self):
        """Test handling non-dict items in content array."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        "string item",  # Not a dict
                        {"type": "text", "text": "Valid text"},
                    ],
                }
            ],
            "max_tokens": 1024,
        }

        result = anthropic_to_openai_request(anthropic_req)

        # Should skip non-dict and process valid text
        assert result["messages"][0]["content"] == "Valid text"

    def test_unknown_content_format_passthrough(self):
        """Test that unknown content formats are passed through."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [
                {
                    "role": "user",
                    "content": 12345,  # Unexpected type
                }
            ],
            "max_tokens": 1024,
        }

        result = anthropic_to_openai_request(anthropic_req)

        assert result["messages"][0]["content"] == 12345

    def test_default_max_tokens(self):
        """Test default max_tokens is applied when not specified."""
        from luthien_proxy.utils.constants import DEFAULT_LLM_MAX_TOKENS

        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [{"role": "user", "content": "Hello"}],
        }

        result = anthropic_to_openai_request(anthropic_req)

        assert result["max_tokens"] == DEFAULT_LLM_MAX_TOKENS

    def test_empty_messages(self):
        """Test handling empty messages array."""
        anthropic_req = {
            "model": "claude-3-opus-20240229",
            "messages": [],
            "max_tokens": 1024,
        }

        result = anthropic_to_openai_request(anthropic_req)

        assert result["messages"] == []


class TestOpenAIToAnthropicResponseToolCalls:
    """Test OpenAI to Anthropic response conversion with tool calls."""

    def test_response_with_tool_calls(self):
        """Test converting response with tool_calls to tool_use blocks."""
        from litellm.types.utils import ChatCompletionMessageToolCall, Function

        openai_response = ModelResponse(
            id="test-id",
            created=1234567890,
            model="gpt-4",
            object="chat.completion",
            choices=[
                Choices(
                    index=0,
                    message=Message(
                        role="assistant",
                        content="Let me check that.",
                        tool_calls=[
                            ChatCompletionMessageToolCall(
                                id="call_123",
                                type="function",
                                function=Function(
                                    name="get_weather",
                                    arguments='{"location": "SF"}',
                                ),
                            )
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=Usage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        )

        result = openai_to_anthropic_response(openai_response)

        assert len(result["content"]) == 2
        assert result["content"][0]["type"] == "text"
        assert result["content"][0]["text"] == "Let me check that."
        assert result["content"][1]["type"] == "tool_use"
        assert result["content"][1]["id"] == "call_123"
        assert result["content"][1]["name"] == "get_weather"
        assert result["content"][1]["input"] == {"location": "SF"}
        assert result["stop_reason"] == "tool_use"

    def test_response_with_multiple_tool_calls(self):
        """Test converting response with multiple tool_calls."""
        from litellm.types.utils import ChatCompletionMessageToolCall, Function

        openai_response = ModelResponse(
            id="test-id",
            created=1234567890,
            model="gpt-4",
            object="chat.completion",
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
                                function=Function(name="tool_a", arguments="{}"),
                            ),
                            ChatCompletionMessageToolCall(
                                id="call_2",
                                type="function",
                                function=Function(name="tool_b", arguments='{"x": 1}'),
                            ),
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=Usage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        )

        result = openai_to_anthropic_response(openai_response)

        # No text content since message.content is None
        assert len(result["content"]) == 2
        assert result["content"][0]["type"] == "tool_use"
        assert result["content"][0]["name"] == "tool_a"
        assert result["content"][1]["type"] == "tool_use"
        assert result["content"][1]["name"] == "tool_b"
        assert result["content"][1]["input"] == {"x": 1}

    def test_tool_call_with_dict_arguments(self):
        """Test tool_call where arguments is already a dict."""
        from litellm.types.utils import ChatCompletionMessageToolCall, Function

        openai_response = ModelResponse(
            id="test-id",
            created=1234567890,
            model="gpt-4",
            object="chat.completion",
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
                                function=Function(
                                    name="my_tool",
                                    arguments={"already": "parsed"},  # type: ignore[arg-type]
                                ),
                            ),
                        ],
                    ),
                    finish_reason="tool_calls",
                )
            ],
            usage=Usage(prompt_tokens=5, completion_tokens=10, total_tokens=15),
        )

        result = openai_to_anthropic_response(openai_response)

        assert result["content"][0]["input"] == {"already": "parsed"}

    def test_response_no_content(self):
        """Test response with no text content."""
        openai_response = ModelResponse(
            id="test-id",
            created=1234567890,
            model="gpt-4",
            object="chat.completion",
            choices=[
                Choices(
                    index=0,
                    message=Message(role="assistant", content=None),
                    finish_reason="stop",
                )
            ],
            usage=Usage(prompt_tokens=5, completion_tokens=0, total_tokens=5),
        )

        result = openai_to_anthropic_response(openai_response)

        # No content blocks when message.content is None/empty
        assert result["content"] == []
