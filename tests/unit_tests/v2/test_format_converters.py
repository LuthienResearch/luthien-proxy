# ABOUTME: Unit tests for LLM format conversion functions
# ABOUTME: Tests OpenAI/Anthropic bidirectional format transformations

"""Tests for LLM format converters."""

from litellm.types.utils import Choices, Message, ModelResponse, Usage

from luthien_proxy.v2.llm.llm_format_utils import (
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
