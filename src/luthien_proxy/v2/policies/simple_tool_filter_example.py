# ABOUTME: Example SimpleEventBasedPolicy that filters dangerous tool calls
# ABOUTME: Demonstrates tool call filtering with complete block inspection

"""Example policy that filters tool calls using SimpleEventBasedPolicy.

This policy demonstrates:
- Inspecting complete tool call blocks (name + arguments)
- Blocking specific tool calls by returning None
- Parsing JSON arguments safely
- Emitting events for observability

This is a more advanced example than simple_uppercase_example, showing
how to work with tool calls.

Usage in config:
    policy:
      class: "luthien_proxy.v2.policies.simple_tool_filter_example:SimpleToolFilterPolicy"
      config:
        blocked_tools:
          - "execute_code"
          - "delete_file"
          - "sudo_command"
"""

from __future__ import annotations

import json
import logging

from luthien_proxy.v2.policies.policy_context import PolicyContext
from luthien_proxy.v2.policies.simple_event_based_policy import (
    SimpleEventBasedPolicy,
    StreamingContext,
)
from luthien_proxy.v2.streaming.stream_blocks import ToolCallStreamBlock

logger = logging.getLogger(__name__)


class SimpleToolFilterPolicy(SimpleEventBasedPolicy):
    """Example policy that blocks dangerous tool calls.

    Demonstrates:
    - Configurable blocklist of tool names
    - Inspecting complete tool call blocks
    - Filtering (returning None to block)
    - Event emission for monitoring
    - Safe JSON parsing

    Config:
        blocked_tools: List of tool names to block (default: ["execute_code"])
    """

    def __init__(self, blocked_tools: list[str] | None = None):
        """Initialize policy with blocklist.

        Args:
            blocked_tools: List of tool names to block. Defaults to ["execute_code"].
        """
        super().__init__()
        self.blocked_tools = set(blocked_tools or ["execute_code"])
        logger.info(f"Initialized SimpleToolFilterPolicy with blocked tools: {self.blocked_tools}")

    async def on_tool_call_simple(
        self,
        tool_call: ToolCallStreamBlock,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> ToolCallStreamBlock | None:
        """Filter tool calls based on blocklist.

        Returns:
            Modified tool call block, or None to block the tool call
        """
        # Check if tool is blocked
        if tool_call.name in self.blocked_tools:
            # Parse arguments for logging (safely)
            try:
                args = json.loads(tool_call.arguments)
                args_summary = str(args)[:100]  # First 100 chars
            except json.JSONDecodeError:
                args_summary = "<invalid JSON>"

            # Emit blocking event
            context.emit(
                event_type="policy.tool_call_blocked",
                summary=f"Blocked dangerous tool call: {tool_call.name}",
                details={
                    "tool_name": tool_call.name,
                    "tool_id": tool_call.id,
                    "arguments_preview": args_summary,
                    "reason": "tool in blocklist",
                },
                severity="warning",
            )

            logger.warning(
                f"Blocked tool call '{tool_call.name}' (id={tool_call.id}) - in blocklist",
            )

            # Return None to block this tool call
            return None

        # Allow tool call (pass through)
        return tool_call

    # Content passes through unchanged (default behavior)


__all__ = ["SimpleToolFilterPolicy"]
