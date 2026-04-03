"""Policy that injects a conversation viewer link into the first response of each conversation.

Prepends a conversation viewer URL to the first text content block on the
first turn of a conversation. Detects "first turn" by checking if the request
contains only a single user message (no prior assistant/user exchanges) —
the same approach used by OnboardingPolicy.

Configuration:
    base_url: The proxy's base URL (e.g., "http://localhost:8000").

Example YAML:
    policy:
      class: "luthien_proxy.policies.conversation_link_policy:ConversationLinkPolicy"
      config:
        base_url: "http://localhost:8000"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import quote

from pydantic import BaseModel, Field

from luthien_proxy.policies.onboarding_policy import is_first_turn
from luthien_proxy.policies.simple_policy import SimplePolicy

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import AnthropicRequest
    from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)


class ConversationLinkPolicyConfig(BaseModel):
    base_url: str = Field(
        default="http://localhost:8000",
        description="Base URL of the Luthien proxy for building viewer links",
    )


@dataclass
class _ConversationLinkState:
    """Request-scoped state: caches whether this is the first conversation turn."""

    first_turn: bool = field(default=False)
    injected: bool = field(default=False)


class ConversationLinkPolicy(SimplePolicy):
    """Injects a conversation viewer link into the first response of each conversation."""

    def __init__(self, base_url: str = "http://localhost:8000", **kwargs: object) -> None:
        """Initialize with base URL for building viewer links."""
        self.config = ConversationLinkPolicyConfig(base_url=base_url)

    @property
    def short_policy_name(self) -> str:
        """Return short name for logging and UI display."""
        return "ConversationLink"

    def _state(self, context: PolicyContext) -> _ConversationLinkState:
        return context.get_request_state(self, _ConversationLinkState, _ConversationLinkState)

    async def on_anthropic_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
        """Cache first-turn check so simple_on_response_content doesn't recompute.

        Preflight calls (max_tokens=1) also match is_first_turn(), but each
        request gets its own PolicyContext so the injection is harmlessly
        scoped to the preflight response the user never sees.
        """
        self._state(context).first_turn = is_first_turn(request)
        return await super().on_anthropic_request(request, context)

    async def simple_on_response_content(self, content: str, context: PolicyContext) -> str:
        """Prepend conversation viewer link on the first turn of a conversation."""
        state = self._state(context)
        if not state.first_turn or state.injected:
            return content

        session_id = context.session_id
        if not session_id:
            return content

        state.injected = True
        base = self.config.base_url.rstrip("/")
        link = f"{base}/conversation/live/{quote(session_id, safe='')}"

        context.record_event(
            "policy.conversation_link.injected",
            {"link": link},
        )

        return f"[Conversation viewer: {link}]\n\n{content}"


__all__ = ["ConversationLinkPolicy"]
