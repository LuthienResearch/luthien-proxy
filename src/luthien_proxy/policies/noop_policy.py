"""No-op policy that performs no modifications."""

from __future__ import annotations

import logging

from luthien_proxy.policy_core import (
    AnthropicHookPolicy,
    BasePolicy,
)

logger = logging.getLogger(__name__)


class NoOpPolicy(BasePolicy, AnthropicHookPolicy):
    """No-op policy that passes through all data unchanged.

    Implements AnthropicHookPolicy. All hooks use default passthrough behavior.
    """

    @property
    def short_policy_name(self) -> str:
        """Return 'NoOp'."""
        return "NoOp"

    def active_policy_names(self) -> list[str]:
        """NoOp doesn't modify anything."""
        return []

    async def on_openai_request(self, request: "Request", context: "PolicyContext") -> "Request":
        """Pass through unchanged."""
        return request

    async def on_openai_response(self, response: "ModelResponse", context: "PolicyContext") -> "ModelResponse":
        """Pass through unchanged."""
        return response


__all__ = ["NoOpPolicy"]
