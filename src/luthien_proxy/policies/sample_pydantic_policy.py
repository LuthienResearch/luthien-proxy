"""Sample policy demonstrating Pydantic config models for dynamic form generation.

This is a working no-op policy that passes through all requests unchanged.
It serves as an example for the dynamic form generation system, showing:
- Basic types with constraints (threshold with min/max)
- Password fields (api_key)
- Discriminated unions (rules with type selector)
- Nested objects and arrays
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, Field

from luthien_proxy.llm.types.anthropic import AnthropicRequest, AnthropicResponse
from luthien_proxy.policy_core.anthropic_interface import (
    AnthropicPolicyInterface,
    AnthropicStreamEvent,
)
from luthien_proxy.policy_core.base_policy import BasePolicy
from luthien_proxy.policy_core.openai_interface import OpenAIPolicyInterface

if TYPE_CHECKING:
    from litellm.types.utils import ModelResponse

    from luthien_proxy.llm.types import Request
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext

logger = logging.getLogger(__name__)


class RegexRuleConfig(BaseModel):
    """Rule that matches content against a regex pattern."""

    type: Literal["regex"] = "regex"
    pattern: str = Field(description="Regular expression pattern to match")
    case_sensitive: bool = Field(default=False, description="Whether matching is case-sensitive")


class KeywordRuleConfig(BaseModel):
    """Rule that matches content against a list of keywords."""

    type: Literal["keyword"] = "keyword"
    keywords: list[str] = Field(description="Keywords to detect in content")


RuleConfig = Annotated[RegexRuleConfig | KeywordRuleConfig, Field(discriminator="type")]


class SampleConfig(BaseModel):
    """Configuration for the sample policy."""

    name: str = Field(default="default", description="Name for this policy instance")
    enabled: bool = Field(default=True, description="Whether the policy is active")
    threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="Detection threshold (0-1)")
    api_key: str | None = Field(default=None, json_schema_extra={"format": "password"})
    rules: list[RuleConfig] = Field(default_factory=list, description="List of detection rules")


class SamplePydanticPolicy(BasePolicy, OpenAIPolicyInterface, AnthropicPolicyInterface):
    """Sample policy demonstrating Pydantic-based configuration.

    This is a working no-op policy that passes through all requests unchanged.
    It serves as an example for the dynamic form generation system, showing:
    - Basic types with constraints (threshold with min/max)
    - Password fields (api_key)
    - Discriminated unions (rules with type selector)
    - Nested objects and arrays
    """

    def __init__(self, config: SampleConfig | None = None):
        """Initialize the policy with optional config.

        Args:
            config: A SampleConfig instance or None for defaults.
                   Also accepts a dict at runtime which will be parsed into SampleConfig.
        """
        self.config = self._init_config(config, SampleConfig)

    # get_config() is inherited from BasePolicy - automatically serializes
    # the self.config Pydantic model

    # -- OpenAI interface hooks (passthrough) ----------------------------------

    async def on_openai_request(self, request: Request, context: PolicyContext) -> Request:
        """Pass through unchanged."""
        return request

    async def on_openai_response(self, response: ModelResponse, context: PolicyContext) -> ModelResponse:
        """Pass through unchanged."""
        return response

    async def on_chunk_received(self, ctx: StreamingPolicyContext) -> None:
        """Pass through chunk unchanged."""
        ctx.push_chunk(ctx.last_chunk_received)

    async def on_content_delta(self, ctx: StreamingPolicyContext) -> None:
        """No-op."""
        pass

    async def on_content_complete(self, ctx: StreamingPolicyContext) -> None:
        """No-op."""
        pass

    async def on_tool_call_delta(self, ctx: StreamingPolicyContext) -> None:
        """No-op."""
        pass

    async def on_tool_call_complete(self, ctx: StreamingPolicyContext) -> None:
        """No-op."""
        pass

    async def on_finish_reason(self, ctx: StreamingPolicyContext) -> None:
        """No-op."""
        pass

    async def on_stream_complete(self, ctx: StreamingPolicyContext) -> None:
        """No-op."""
        pass

    async def on_streaming_policy_complete(self, ctx: StreamingPolicyContext) -> None:
        """No-op."""
        pass

    # -- Anthropic interface hooks (passthrough) -------------------------------

    async def on_anthropic_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
        """Pass through unchanged."""
        return request

    async def on_anthropic_response(self, response: AnthropicResponse, context: PolicyContext) -> AnthropicResponse:
        """Pass through unchanged."""
        return response

    async def on_anthropic_stream_event(
        self, event: AnthropicStreamEvent, context: PolicyContext
    ) -> list[AnthropicStreamEvent]:
        """Pass through unchanged."""
        return [event]


__all__ = [
    "SamplePydanticPolicy",
    "SampleConfig",
    "RuleConfig",
    "RegexRuleConfig",
    "KeywordRuleConfig",
]
