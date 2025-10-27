# ABOUTME: Example SimpleEventBasedPolicy that uppercases all response content
# ABOUTME: Demonstrates beginner-friendly interface for simple transformations

"""Example policy using SimpleEventBasedPolicy.

This policy demonstrates how easy it is to write policies using the simplified
interface. It uppercases all text content from responses while passing tool
calls through unchanged.

This is a minimal example showing:
- How to override just one hook (on_response_content)
- How transformations work with buffered content
- How tool calls pass through by default

Usage in config:
    policy:
      class: "luthien_proxy.v2.policies.simple_uppercase_example:SimpleUppercasePolicy"
      config: {}
"""

from __future__ import annotations

from luthien_proxy.v2.policies.policy_context import PolicyContext
from luthien_proxy.v2.policies.simple_event_based_policy import (
    SimpleEventBasedPolicy,
    StreamingContext,
)


class SimpleUppercasePolicy(SimpleEventBasedPolicy):
    """Example policy that uppercases all response content.

    This demonstrates the simplest possible SimpleEventBasedPolicy implementation:
    - Override one hook (on_response_content)
    - Transform the complete content text
    - Tool calls pass through unchanged (default behavior)

    Perfect for learning the SimpleEventBasedPolicy interface.
    """

    async def on_response_content(
        self,
        content: str,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> str:
        """Uppercase all content text."""
        # Emit event for observability
        context.emit(
            event_type="policy.content_transformed",
            summary="Uppercased response content",
            details={
                "original_length": len(content),
                "transformation": "uppercase",
            },
        )

        return content.upper()

    # No need to override:
    # - on_request: passes through unchanged (default)
    # - on_response_tool_call: passes through unchanged (default)


__all__ = ["SimpleUppercasePolicy"]
