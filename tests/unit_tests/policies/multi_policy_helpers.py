"""Shared test helpers for multi-policy tests."""

from __future__ import annotations

from litellm.types.utils import Choices, Message, ModelResponse

from luthien_proxy.llm.types.anthropic import (
    AnthropicResponse,
    AnthropicTextBlock,
)
from luthien_proxy.policy_core import (
    AnthropicPolicyInterface,
    BasePolicy,
    OpenAIPolicyInterface,
)


class OpenAIOnlyPolicy(BasePolicy, OpenAIPolicyInterface):
    """Stub policy implementing only OpenAIPolicyInterface (not Anthropic)."""

    @property
    def short_policy_name(self) -> str:
        return "OpenAIOnly"

    async def on_openai_request(self, request, context):
        return request

    async def on_openai_response(self, response, context):
        return response

    async def on_chunk_received(self, ctx):
        pass

    async def on_content_delta(self, ctx):
        pass

    async def on_content_complete(self, ctx):
        pass

    async def on_tool_call_delta(self, ctx):
        pass

    async def on_tool_call_complete(self, ctx):
        pass

    async def on_finish_reason(self, ctx):
        pass

    async def on_stream_complete(self, ctx):
        pass

    async def on_streaming_policy_complete(self, ctx):
        pass


class AnthropicOnlyPolicy(BasePolicy, AnthropicPolicyInterface):
    """Stub policy implementing only AnthropicPolicyInterface (not OpenAI)."""

    @property
    def short_policy_name(self) -> str:
        return "AnthropicOnly"

    async def on_anthropic_request(self, request, context):
        return request

    async def on_anthropic_response(self, response, context):
        return response

    async def on_anthropic_stream_event(self, event, context):
        return [event]


def noop_config() -> dict:
    return {"class": "luthien_proxy.policies.noop_policy:NoOpPolicy", "config": {}}


def allcaps_config() -> dict:
    return {"class": "luthien_proxy.policies.all_caps_policy:AllCapsPolicy", "config": {}}


def replacement_config(replacements: list[list[str]]) -> dict:
    return {
        "class": "luthien_proxy.policies.string_replacement_policy:StringReplacementPolicy",
        "config": {"replacements": replacements},
    }


def make_response(content: str) -> ModelResponse:
    return ModelResponse(
        id="test-id",
        choices=[
            Choices(
                finish_reason="stop",
                index=0,
                message=Message(content=content, role="assistant"),
            )
        ],
        created=1234567890,
        model="test-model",
        object="chat.completion",
    )


def make_anthropic_response(text: str) -> AnthropicResponse:
    block: AnthropicTextBlock = {"type": "text", "text": text}
    return {
        "id": "msg_123",
        "type": "message",
        "role": "assistant",
        "content": [block],
        "model": "claude-sonnet-4-20250514",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
