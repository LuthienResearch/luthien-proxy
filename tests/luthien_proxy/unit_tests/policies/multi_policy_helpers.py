"""Shared test helpers for multi-policy tests."""

from __future__ import annotations

from litellm.types.utils import Choices, Message, ModelResponse
from tests.constants import DEFAULT_TEST_MODEL

from luthien_proxy.llm.types.anthropic import (
    AnthropicResponse,
    AnthropicTextBlock,
)
from luthien_proxy.policy_core import (
    AnthropicHookPolicy,
    BasePolicy,
)


class OpenAIOnlyPolicy(BasePolicy):
    """Stub policy implementing neither OpenAIPolicyInterface nor AnthropicExecutionInterface."""

    @property
    def short_policy_name(self) -> str:
        return "OpenAIOnly"


class AnthropicOnlyPolicy(BasePolicy, AnthropicHookPolicy):
    """Stub policy implementing only the Anthropic hooks (not OpenAI)."""

    @property
    def short_policy_name(self) -> str:
        return "AnthropicOnly"


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
        "model": DEFAULT_TEST_MODEL,
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
