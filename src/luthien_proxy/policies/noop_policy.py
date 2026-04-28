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

    category = "internal"
    display_name = "Passthrough"
    short_description = "Passes through all data unchanged."

    @property
    def short_policy_name(self) -> str:
        """Return 'NoOp'."""
        return "NoOp"

    def active_policy_names(self) -> list[str]:
        """NoOp doesn't modify anything."""
        return []


__all__ = ["NoOpPolicy"]
