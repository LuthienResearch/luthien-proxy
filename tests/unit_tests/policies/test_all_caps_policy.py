"""Unit tests for AllCapsPolicy.

Since AllCapsPolicy is a thin TextModifierPolicy subclass, these tests focus on:
- Protocol compliance (correct base classes)
- modify_text correctness
- Integration through OpenAI and Anthropic non-streaming paths

Streaming plumbing is tested by TextModifierPolicy's own tests.
"""

from typing import cast

import pytest
from litellm.types.utils import Choices, Message, ModelResponse

from conftest import DEFAULT_TEST_MODEL
from luthien_proxy.llm.types import Request
from luthien_proxy.llm.types.anthropic import (
    AnthropicResponse,
    AnthropicTextBlock,
    AnthropicToolUseBlock,
)
from luthien_proxy.policies.all_caps_policy import AllCapsPolicy
from luthien_proxy.policy_core import (
    AnthropicExecutionInterface,
    OpenAIPolicyInterface,
    TextModifierPolicy,
)
from luthien_proxy.policy_core.base_policy import BasePolicy
from luthien_proxy.policy_core.policy_context import PolicyContext


@pytest.fixture
def policy():
    """Create an AllCapsPolicy instance."""
    return AllCapsPolicy()


@pytest.fixture
def policy_context():
    """Create a basic policy context."""
    return PolicyContext(
        transaction_id="test-txn-123",
        request=Request(
            model="test-model",
            messages=[{"role": "user", "content": "test"}],
        ),
    )


# =============================================================================
# Protocol Compliance Tests
# =============================================================================


class TestAllCapsPolicyProtocol:
    """Tests verifying AllCapsPolicy implements the required interfaces."""

    def test_inherits_from_text_modifier_policy(self, policy):
        assert isinstance(policy, TextModifierPolicy)

    def test_inherits_from_base_policy(self, policy):
        assert isinstance(policy, BasePolicy)

    def test_implements_openai_interface(self, policy):
        assert isinstance(policy, OpenAIPolicyInterface)

    def test_implements_anthropic_interface(self, policy):
        assert isinstance(policy, AnthropicExecutionInterface)


# =============================================================================
# modify_text Tests
# =============================================================================


class TestModifyText:
    """Test the core text transformation."""

    def test_lowercase_to_uppercase(self, policy):
        assert policy.modify_text("hello world") == "HELLO WORLD"

    def test_mixed_case(self, policy):
        assert policy.modify_text("HeLLo WoRLd") == "HELLO WORLD"

    def test_already_uppercase(self, policy):
        assert policy.modify_text("HELLO WORLD") == "HELLO WORLD"

    def test_empty_string(self, policy):
        assert policy.modify_text("") == ""

    def test_special_characters_preserved(self, policy):
        assert policy.modify_text("Hello, world!\n\t123 + 456 = 579") == "HELLO, WORLD!\n\t123 + 456 = 579"

    def test_numbers_and_symbols(self, policy):
        assert policy.modify_text("123 abc 456") == "123 ABC 456"

    def test_email_format(self, policy):
        assert policy.modify_text("test@example.com") == "TEST@EXAMPLE.COM"

    def test_camel_case(self, policy):
        assert policy.modify_text("CamelCaseText") == "CAMELCASETEXT"


# =============================================================================
# OpenAI Non-Streaming Tests
# =============================================================================


class TestAllCapsPolicyOpenAINonStreaming:
    """Test OpenAI non-streaming response handling."""

    async def test_uppercase_text_response(self, policy, policy_context):
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=Message(content="hello world", role="assistant"),
                )
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_openai_response(response, policy_context)
        assert result.choices[0].message.content == "HELLO WORLD"

    async def test_uppercase_multiple_choices(self, policy, policy_context):
        response = ModelResponse(
            id="test-id",
            choices=[
                Choices(finish_reason="stop", index=0, message=Message(content="hello world", role="assistant")),
                Choices(finish_reason="stop", index=1, message=Message(content="goodbye world", role="assistant")),
            ],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_openai_response(response, policy_context)
        assert result.choices[0].message.content == "HELLO WORLD"
        assert result.choices[1].message.content == "GOODBYE WORLD"

    async def test_empty_content(self, policy, policy_context):
        response = ModelResponse(
            id="test-id",
            choices=[Choices(finish_reason="stop", index=0, message=Message(content=None, role="assistant"))],
            created=1234567890,
            model="test-model",
            object="chat.completion",
        )

        result = await policy.on_openai_response(response, policy_context)
        assert result.choices[0].message.content is None

    async def test_no_choices(self, policy, policy_context):
        response = ModelResponse(
            id="test-id", choices=[], created=1234567890, model="test-model", object="chat.completion"
        )

        result = await policy.on_openai_response(response, policy_context)
        assert len(result.choices) == 0

    async def test_request_unchanged(self, policy, policy_context):
        request = Request(
            model="test-model",
            messages=[
                {"role": "user", "content": "hello world"},
                {"role": "assistant", "content": "goodbye world"},
            ],
        )

        result = await policy.on_openai_request(request, policy_context)
        assert result == request


# =============================================================================
# Anthropic Non-Streaming Response Tests
# =============================================================================


class TestAllCapsPolicyAnthropicResponse:
    """Tests for Anthropic non-streaming response transformation via run_anthropic."""

    @pytest.mark.asyncio
    async def test_transforms_text_to_uppercase(self):
        policy = AllCapsPolicy()

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

        # TextModifierPolicy handles Anthropic via _modify_anthropic_response
        policy._modify_anthropic_response(response)

        result_text_block = cast(AnthropicTextBlock, response["content"][0])
        assert result_text_block["text"] == "HELLO, WORLD!"

    @pytest.mark.asyncio
    async def test_transforms_multiple_text_blocks(self):
        policy = AllCapsPolicy()

        text_block1: AnthropicTextBlock = {"type": "text", "text": "First block"}
        text_block2: AnthropicTextBlock = {"type": "text", "text": "Second block"}
        response: AnthropicResponse = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [text_block1, text_block2],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 10},
        }

        policy._modify_anthropic_response(response)

        assert cast(AnthropicTextBlock, response["content"][0])["text"] == "FIRST BLOCK"
        assert cast(AnthropicTextBlock, response["content"][1])["text"] == "SECOND BLOCK"

    @pytest.mark.asyncio
    async def test_leaves_tool_use_unchanged(self):
        policy = AllCapsPolicy()

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

        policy._modify_anthropic_response(response)

        result_tool_block = cast(AnthropicToolUseBlock, response["content"][0])
        assert result_tool_block["type"] == "tool_use"
        assert result_tool_block["name"] == "get_weather"
        assert result_tool_block["input"] == {"location": "San Francisco"}

    @pytest.mark.asyncio
    async def test_mixed_content_blocks(self):
        policy = AllCapsPolicy()

        text_block: AnthropicTextBlock = {"type": "text", "text": "Let me check the weather"}
        tool_use_block: AnthropicToolUseBlock = {
            "type": "tool_use",
            "id": "tool_456",
            "name": "get_weather",
            "input": {"location": "NYC"},
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

        policy._modify_anthropic_response(response)

        assert cast(AnthropicTextBlock, response["content"][0])["text"] == "LET ME CHECK THE WEATHER"
        result_tool_block = cast(AnthropicToolUseBlock, response["content"][1])
        assert result_tool_block["type"] == "tool_use"
        assert result_tool_block["name"] == "get_weather"


__all__ = [
    "TestAllCapsPolicyProtocol",
    "TestModifyText",
    "TestAllCapsPolicyOpenAINonStreaming",
    "TestAllCapsPolicyAnthropicResponse",
]
