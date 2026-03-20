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

from pydantic import Field

from luthien_proxy.policies.onboarding_policy import OnboardingPolicy, OnboardingPolicyConfig


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


class HackathonOnboardingPolicyConfig(OnboardingPolicyConfig):
    """Configuration for HackathonOnboardingPolicy."""

    gateway_url: str = Field(default="http://localhost:8000", description="Gateway URL for config UI links")


class HackathonOnboardingPolicy(OnboardingPolicy):
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
        """Return the hackathon welcome message."""
        return self._welcome


__all__ = ["HackathonOnboardingPolicy", "HackathonOnboardingPolicyConfig"]
