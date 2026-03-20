"""OnboardingPolicy - Welcome message on first conversation turn.

Appends a welcome message with Luthien setup info to the first response
in a conversation. Detects "first turn" by checking if the request contains
only a single user message (no prior assistant/user exchanges).

After the first turn, the policy is completely inert.

Example config:
    policy:
      class: "luthien_proxy.policies.onboarding_policy:OnboardingPolicy"
      config:
        gateway_url: "http://localhost:8000"
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from luthien_proxy.policy_core import BasePolicy, TextModifierPolicy

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from luthien_proxy.llm.types.anthropic import AnthropicRequest, AnthropicResponse
    from luthien_proxy.policy_core import (
        AnthropicPolicyEmission,
        AnthropicPolicyIOProtocol,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)


WELCOME_MESSAGE = """

---

**Welcome to Luthien!** Your proxy is running and intercepting API traffic.

**What just happened:** This message was appended by the *onboarding policy* — \
a policy that only activates on the first turn of each conversation. \
Every response after this one passes through unmodified.

**Configure your proxy:** [{gateway_url}/policy-config]({gateway_url}/policy-config)
From there you can swap in different policies — content filters, safety checks, \
tool call judges, or write your own.

**Quick reference:**
- `!luthien status` — check gateway health
- `!luthien logs` — view gateway logs
- `!luthien config` — manage settings
- `!luthien down` / `!luthien up` — stop/start the gateway

---"""


class OnboardingPolicyConfig(BaseModel):
    """Configuration for OnboardingPolicy."""

    gateway_url: str = Field(default="http://localhost:8000", description="Gateway URL for config UI links")


def is_first_turn(request: dict) -> bool:
    """Check if this is the first turn of a conversation.

    First turn = exactly one user message with no prior assistant responses.
    """
    messages = request.get("messages", [])
    if not messages:
        return False

    user_messages = [m for m in messages if m.get("role") == "user"]
    assistant_messages = [m for m in messages if m.get("role") == "assistant"]

    return len(user_messages) == 1 and len(assistant_messages) == 0


class OnboardingPolicy(TextModifierPolicy):
    """Appends a welcome message to the first response in a conversation.

    On subsequent turns (when the request contains prior assistant messages),
    the policy passes everything through unchanged.
    """

    def __init__(self, config: OnboardingPolicyConfig | dict | None = None):
        self.config = self._init_config(config, OnboardingPolicyConfig)
        self._gateway_url = self.config.gateway_url.rstrip("/")
        self._welcome = WELCOME_MESSAGE.format(gateway_url=self._gateway_url)

    def extra_text(self) -> str | None:
        """Return the welcome message (only used when _should_activate is True)."""
        return self._welcome

    def run_anthropic(
        self, io: AnthropicPolicyIOProtocol, context: PolicyContext
    ) -> AsyncIterator[AnthropicPolicyEmission]:
        """Only apply text modification on the first turn; passthrough otherwise."""
        if is_first_turn(io.request):
            return super().run_anthropic(io, context)

        # Not first turn — pure passthrough
        return self._passthrough(io)

    async def _passthrough(
        self, io: AnthropicPolicyIOProtocol
    ) -> AsyncIterator[AnthropicPolicyEmission]:
        """Stream or complete with zero modifications."""
        request = io.request
        if request.get("stream", False):
            async for event in io.stream(request):
                yield event
        else:
            yield await io.complete(request)

    async def on_anthropic_request(
        self, request: AnthropicRequest, context: PolicyContext
    ) -> AnthropicRequest:
        return request

    async def on_anthropic_response(
        self, response: AnthropicResponse, context: PolicyContext
    ) -> AnthropicResponse:
        """For non-streaming in MultiSerialPolicy composition: only modify on first turn."""
        if context.request and is_first_turn(context.request):
            return await super().on_anthropic_response(response, context)
        return response

    async def on_anthropic_stream_event(self, event, context: PolicyContext):
        """For streaming in MultiSerialPolicy composition: passthrough (extra_text handles the append)."""
        if context.request and is_first_turn(context.request):
            return await super().on_anthropic_stream_event(event, context)
        return [event]

    async def on_anthropic_stream_complete(self, context: PolicyContext):
        """Emit welcome text block after stream ends, but only on first turn."""
        if context.request and is_first_turn(context.request):
            return await super().on_anthropic_stream_complete(context)
        return []


__all__ = ["OnboardingPolicy", "OnboardingPolicyConfig", "is_first_turn"]
