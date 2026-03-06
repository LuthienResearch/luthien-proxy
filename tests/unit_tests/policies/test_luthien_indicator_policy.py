"""Unit tests for LuthienIndicatorPolicy."""

from __future__ import annotations

import pytest
from litellm.types.utils import ModelResponse

from luthien_proxy.llm.types import Request
from luthien_proxy.policies.luthien_indicator_policy import (
    INDICATOR_SUFFIX,
    LuthienIndicatorPolicy,
)
from luthien_proxy.policy_core import (
    AnthropicExecutionInterface,
    BasePolicy,
    OpenAIPolicyInterface,
)
from luthien_proxy.policy_core.policy_context import PolicyContext


class TestLuthienIndicatorPolicyInit:
    """Tests for policy initialization and config."""

    def test_inherits_correct_classes(self):
        policy = LuthienIndicatorPolicy()
        assert isinstance(policy, BasePolicy)
        assert isinstance(policy, OpenAIPolicyInterface)
        assert isinstance(policy, AnthropicExecutionInterface)

    def test_short_policy_name(self):
        policy = LuthienIndicatorPolicy()
        assert policy.short_policy_name == "LuthienIndicator"

    def test_default_indicator(self):
        policy = LuthienIndicatorPolicy()
        assert policy._indicator == INDICATOR_SUFFIX

    def test_custom_indicator(self):
        policy = LuthienIndicatorPolicy(config={"indicator": "\n[Custom]"})
        assert policy._indicator == "\n[Custom]"


class TestOpenAINonStreaming:
    """Tests for OpenAI non-streaming response modification."""

    @pytest.mark.asyncio
    async def test_appends_indicator_to_text(self, make_model_response):
        policy = LuthienIndicatorPolicy()
        ctx = PolicyContext.for_testing()
        response = make_model_response(content="Hello!")

        result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "Hello!" + INDICATOR_SUFFIX

    @pytest.mark.asyncio
    async def test_empty_choices_unchanged(self):
        policy = LuthienIndicatorPolicy()
        ctx = PolicyContext.for_testing()
        response = ModelResponse(id="test", created=0, model="gpt-4", object="chat.completion", choices=[])

        result = await policy.on_openai_response(response, ctx)

        assert len(result.choices) == 0

    @pytest.mark.asyncio
    async def test_request_passes_through(self):
        policy = LuthienIndicatorPolicy()
        ctx = PolicyContext.for_testing()
        request = Request(model="gpt-4", messages=[{"role": "user", "content": "Hi"}])

        result = await policy.on_openai_request(request, ctx)

        assert result is request

    @pytest.mark.asyncio
    async def test_custom_indicator_appended(self, make_model_response):
        policy = LuthienIndicatorPolicy(config={"indicator": " [LOGGED]"})
        ctx = PolicyContext.for_testing()
        response = make_model_response(content="Hello!")

        result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "Hello! [LOGGED]"


class TestAnthropicNonStreaming:
    """Tests for Anthropic non-streaming response modification."""

    def test_appends_text_block(self):
        policy = LuthienIndicatorPolicy()
        response = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello!"}],
            "model": "claude-haiku-4-5-20251001",
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = policy._append_indicator_to_anthropic(response)

        assert len(result["content"]) == 2
        assert result["content"][0]["text"] == "Hello!"
        assert result["content"][1]["type"] == "text"
        assert result["content"][1]["text"] == INDICATOR_SUFFIX

    def test_preserves_tool_use_blocks(self):
        policy = LuthienIndicatorPolicy()
        tool_block = {"type": "tool_use", "id": "tool_1", "name": "search", "input": {"q": "test"}}
        response = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [tool_block],
            "model": "claude-haiku-4-5-20251001",
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        result = policy._append_indicator_to_anthropic(response)

        assert len(result["content"]) == 2
        assert result["content"][0] == tool_block
        assert result["content"][1]["type"] == "text"

    def test_empty_content_gets_indicator(self):
        policy = LuthienIndicatorPolicy()
        response = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": "claude-haiku-4-5-20251001",
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 0},
        }

        result = policy._append_indicator_to_anthropic(response)

        assert len(result["content"]) == 1
        assert result["content"][0]["text"] == INDICATOR_SUFFIX


__all__ = []
