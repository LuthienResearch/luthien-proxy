"""UppercasePolicy — Simplest possible TextModifierPolicy.

Converts all text content in responses to uppercase.
Tool calls, thinking blocks, and images pass through unchanged.

Example config:
    policy:
      class: "luthien_proxy.policies.uppercase_policy:UppercasePolicy"
      config: {}
"""

from __future__ import annotations

from luthien_proxy.policy_core import TextModifierPolicy


class UppercasePolicy(TextModifierPolicy):
    """Convert all response text to uppercase."""

    def modify_text(self, text: str) -> str:
        return text.upper()
