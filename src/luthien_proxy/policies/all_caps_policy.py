"""AllCapsPolicy - Simple content transformation example.

Converts all text content in responses to uppercase using TextModifierPolicy.

Example config:
    policy:
      class: "luthien_proxy.policies.all_caps_policy:AllCapsPolicy"
      config: {}
"""

from __future__ import annotations

from luthien_proxy.policy_core import TextModifierPolicy


class AllCapsPolicy(TextModifierPolicy):
    """Policy that converts all response content to uppercase.

    Tool calls, thinking blocks, and images pass through unchanged.
    """

    def modify_text(self, text: str) -> str:
        """Convert text to uppercase."""
        return text.upper()


__all__ = ["AllCapsPolicy"]
