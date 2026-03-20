"""HackathonOnboardingPolicy - Welcome message with hackathon context on first turn.

Appends a welcome message with hackathon-specific guidance to the first response
in a conversation. Detects "first turn" by checking if the request contains
only a single user message (no prior assistant/user exchanges).

After the first turn, the policy is completely inert.

Example config:
    policy:
      class: "luthien_proxy.policies.hackathon_onboarding_policy:HackathonOnboardingPolicy"
      config:
        gateway_url: "http://localhost:8000"
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from luthien_proxy.policies.onboarding_policy import is_first_turn
from luthien_proxy.policy_core import TextModifierPolicy

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from anthropic.lib.streaming import MessageStreamEvent

    from luthien_proxy.llm.types.anthropic import AnthropicRequest, AnthropicResponse
    from luthien_proxy.policy_core import (
        AnthropicPolicyEmission,
        AnthropicPolicyIOProtocol,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext


WELCOME_MESSAGE = """

---

**Welcome to the Luthien Hackathon!** Your proxy is running and intercepting API traffic.

**What Luthien is:** Luthien is an AI control framework that lets you write policies to intercept, \
inspect, and modify LLM requests and responses. The proxy you're interacting with is a FastAPI \
gateway that loads and executes policies on every API call.

**How to develop:** Edit a policy file → the gateway automatically reloads it (or restart if needed). \
Your changes take effect immediately on the next request.

**Key files to explore:**
- `hackathon_policy_template.py` — Template for your first policy (copy and modify)
- `all_caps_policy.py` — Simple policy that makes responses ALL CAPS (great example)
- `text_modifier_policy.py` — Base class for text-modification policies

**Top 5 project ideas:**
1. **Resampling policy** — Re-run the same request multiple times, pick the best response
2. **Trusted model reroute** — Check request origin; reroute untrusted callers to a smaller model
3. **Proxy commands** — Implement `!luthien ask-policy` to ask the policy for recommendations
4. **Live policy editor** — Admin UI where you can edit and test policies in real-time
5. **Character injection** — Make all responses written in the style of a character (Shakespeare, pirate, etc.)

**Configure your proxy:** [{gateway_url}/policy-config]({gateway_url}/policy-config) — \
swap policies, tweak settings, and reload without restarting.

**Monitor activity:** [{gateway_url}/activity]({gateway_url}/activity) — \
view conversation events, diffs, and policy execution traces.

**Hackathon page:** [Luthien Hackathon](https://luthien.dev/hackathon)

---"""


class HackathonOnboardingPolicyConfig(BaseModel):
    """Configuration for HackathonOnboardingPolicy."""

    gateway_url: str = Field(default="http://localhost:8000", description="Gateway URL for config UI links")


class HackathonOnboardingPolicy(TextModifierPolicy):
    """Appends a hackathon-focused welcome message to the first response in a conversation.

    On subsequent turns (when the request contains prior assistant messages),
    the policy passes everything through unchanged.
    """

    def __init__(self, config: HackathonOnboardingPolicyConfig | dict | None = None):
        """Initialize with optional config. Accepts dict or Pydantic model."""
        self.config = self._init_config(config, HackathonOnboardingPolicyConfig)
        self._gateway_url = self.config.gateway_url.rstrip("/")
        self._welcome = WELCOME_MESSAGE.format(gateway_url=self._gateway_url)

    def extra_text(self) -> str | None:
        """Return the welcome message. All callers are gated by is_first_turn()."""
        return self._welcome

    def run_anthropic(
        self, io: AnthropicPolicyIOProtocol, context: PolicyContext
    ) -> AsyncIterator[AnthropicPolicyEmission]:
        """Only apply text modification on the first turn; passthrough otherwise.

        Intentionally a plain def (not async def) — both branches return
        async iterators directly.
        """
        if is_first_turn(io.request):
            return super().run_anthropic(io, context)

        return self._passthrough(io)

    async def _passthrough(self, io: AnthropicPolicyIOProtocol) -> AsyncGenerator[AnthropicPolicyEmission, None]:
        """Stream or complete with zero modifications."""
        request = io.request
        if request.get("stream", False):
            async for event in io.stream(request):
                yield event
        else:
            yield await io.complete(request)

    async def on_anthropic_request(self, request: AnthropicRequest, context: PolicyContext) -> AnthropicRequest:
        """Pass through request unchanged."""
        return request

    async def on_anthropic_response(self, response: AnthropicResponse, context: PolicyContext) -> AnthropicResponse:
        """For non-streaming in MultiSerialPolicy composition: only modify on first turn."""
        if context.request and is_first_turn(context.request):
            return await super().on_anthropic_response(response, context)
        return response

    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: PolicyContext
    ) -> list[MessageStreamEvent]:
        """For streaming in MultiSerialPolicy composition: passthrough (extra_text handles the append)."""
        if context.request and is_first_turn(context.request):
            return await super().on_anthropic_stream_event(event, context)
        return [event]

    async def on_anthropic_stream_complete(self, context: PolicyContext) -> list[AnthropicPolicyEmission]:
        """Emit welcome text block after stream ends, but only on first turn."""
        if context.request and is_first_turn(context.request):
            return await super().on_anthropic_stream_complete(context)
        return []


__all__ = ["HackathonOnboardingPolicy", "HackathonOnboardingPolicyConfig"]
