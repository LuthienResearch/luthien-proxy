"""FirstTurnBannerPolicy — SimplePolicy with request-scoped state.

Prepends a welcome banner to the first response in each conversation.
Demonstrates: SimplePolicy subclassing, PolicyContext state, first-turn detection.

Example config:
    policy:
      class: "luthien_proxy.policies.first_turn_banner_policy:FirstTurnBannerPolicy"
      config:
        banner: "Welcome to the Luthien-powered assistant!"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from luthien_proxy.policies.onboarding_policy import is_first_turn
from luthien_proxy.policies.simple_policy import SimplePolicy

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import AnthropicRequest
    from luthien_proxy.policy_core.policy_context import PolicyContext


class FirstTurnBannerConfig(BaseModel):
    banner: str = Field(
        default="Welcome! This conversation is monitored by Luthien.",
        description="Text to prepend to the first response",
    )


@dataclass
class _BannerState:
    """Request-scoped state: tracks first-turn detection."""

    is_first_turn: bool = field(default=False)


class FirstTurnBannerPolicy(SimplePolicy):
    """Prepend a banner to the first response in a conversation."""

    def __init__(self, config: FirstTurnBannerConfig | None = None):
        self.config = self._init_config(config, FirstTurnBannerConfig)

    @property
    def short_policy_name(self) -> str:
        return "FirstTurnBanner"

    def _state(self, context: PolicyContext) -> _BannerState:
        return context.get_request_state(self, _BannerState, _BannerState)

    async def on_anthropic_request(
        self, request: AnthropicRequest, context: PolicyContext
    ) -> AnthropicRequest:
        self._state(context).is_first_turn = is_first_turn(request)
        return await super().on_anthropic_request(request, context)

    async def simple_on_response_content(
        self, content: str, context: PolicyContext
    ) -> str:
        if not self._state(context).is_first_turn:
            return content
        return f"[{self.config.banner}]\n\n{content}"
