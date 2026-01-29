"""DeSlop Policy - Remove common AI-isms from responses.

A simple text transformation policy that replaces stylistic patterns
commonly associated with AI-generated text ("slop").
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from luthien_proxy.policies.simple_policy import SimplePolicy

if TYPE_CHECKING:
    from luthien_proxy.policy_core.policy_context import PolicyContext


class DeSlop(SimplePolicy):
    """Remove common AI-isms from LLM responses.

    This policy performs simple text replacements to reduce the "AI feel"
    of responses. Configurable via the `replacements` config option.

    Default replacements:
    - Em-dashes (—) → regular dashes (-)
    - En-dashes (–) → regular dashes (-)

    Example config:
        policy:
          class: "luthien_proxy.policies.deslop_policy:DeSlop"
          config:
            replacements:
              "—": "-"
              "–": "-"
              "utilize": "use"
    """

    # Default replacements - common AI-isms to plain text
    DEFAULT_REPLACEMENTS: dict[str, str] = {
        "—": "-",  # em-dash
        "–": "-",  # en-dash
        "\u2018": "'",  # left single curly quote
        "\u2019": "'",  # right single curly quote (apostrophe)
        "\u201c": '"',  # left double curly quote
        "\u201d": '"',  # right double curly quote
    }

    def __init__(self, config: dict | None = None) -> None:
        """Initialize with optional custom replacements.

        Args:
            config: Optional config dict. Supports:
                - replacements: dict of string -> string replacements
        """
        config = config or {}

        # Merge custom replacements with defaults (custom takes precedence)
        self.replacements = {**self.DEFAULT_REPLACEMENTS, **config.get("replacements", {})}

    @property
    def short_policy_name(self) -> str:
        """Return the short policy name for display in the UI."""
        return "DeSlop"

    async def simple_on_response_content(self, content: str, context: PolicyContext) -> str:
        """Apply text replacements to response content.

        Args:
            content: The complete response content
            context: Policy context for observability

        Returns:
            Content with replacements applied
        """
        original_content = content
        replacement_count = 0

        for pattern, replacement in self.replacements.items():
            count = content.count(pattern)
            if count > 0:
                replacement_count += count
                content = content.replace(pattern, replacement)

        # Log if we made changes
        if replacement_count > 0:
            context.record_event(
                "policy.deslop.replacements_applied",
                {
                    "replacement_count": replacement_count,
                    "original_length": len(original_content),
                    "new_length": len(content),
                },
            )

        return content


# Alias for easier import
DeSlopPolicy = DeSlop

__all__ = ["DeSlop", "DeSlopPolicy"]
