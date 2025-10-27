# ABOUTME: SimpleEventBasedPolicy that performs string replacements on content
# ABOUTME: Configurable map of before->after replacements for testing and simple transformations

"""Simple string replacement policy using SimpleEventBasedPolicy.

This policy performs configurable string replacements on response content
and optionally on tool call data (names and arguments).

It's useful for:
- Testing SimpleEventBasedPolicy with e2e tests
- Simple content transformations (e.g., brand name changes)
- Censoring or redacting specific terms

The policy applies replacements in the order provided, so earlier replacements
can affect later ones.

Usage in config:
    policy:
      class: "luthien_proxy.v2.policies.simple_string_replacement:SimpleStringReplacementPolicy"
      config:
        replacements:
          "OpenAI": "MyCompany"
          "GPT": "MyModel"
          "sensitive_term": "[REDACTED]"
        apply_to_tool_calls: false  # Optional, default is false
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


class SimpleStringReplacementPolicy(SimpleEventBasedPolicy):
    """Policy that performs string replacements on response content and optionally tool calls.

    Applies a configurable dictionary of string replacements to text content.
    Can optionally apply replacements to tool call names and arguments as well.
    Replacements are applied in the order provided (dict insertion order preserved).

    Config:
        replacements: Dict mapping strings to replace (keys) to their replacements (values)
        apply_to_tool_calls: Boolean to enable replacements in tool call data (default: False)

    Example:
        replacements:
          "hello": "goodbye"
          "world": "universe"
        apply_to_tool_calls: true
    """

    def __init__(
        self,
        replacements: dict[str, str] | None = None,
        apply_to_tool_calls: bool = False,
    ):
        """Initialize policy with replacement map.

        Args:
            replacements: Dictionary mapping strings to replace to their replacements.
                         Defaults to empty dict (no replacements).
            apply_to_tool_calls: If True, also apply replacements to tool call names and arguments.
                                Defaults to False.
        """
        super().__init__()
        self.replacements = replacements or {}
        self.apply_to_tool_calls = apply_to_tool_calls

        if not self.replacements:
            logger.warning("SimpleStringReplacementPolicy initialized with no replacements")
        else:
            logger.info(
                f"SimpleStringReplacementPolicy initialized with {len(self.replacements)} replacements "
                f"(apply_to_tool_calls={apply_to_tool_calls}): {list(self.replacements.keys())}"
            )

    async def on_response_content(
        self,
        content: str,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> str:
        """Apply string replacements to content.

        Replacements are applied in order, so earlier replacements can affect
        the input for later replacements.

        Args:
            content: Original content text
            context: Policy context for event emission
            streaming_ctx: Streaming context

        Returns:
            Content with replacements applied
        """
        if not self.replacements:
            return content

        modified = content
        replacements_made = 0

        # Apply each replacement in order
        for before, after in self.replacements.items():
            if before in modified:
                count = modified.count(before)
                modified = modified.replace(before, after)
                replacements_made += count

        # Emit event if any replacements were made
        if replacements_made > 0:
            # Truncate content for event emission (avoid huge events)
            max_content_length = 500
            original_preview = content[:max_content_length]
            if len(content) > max_content_length:
                original_preview += "..."
            modified_preview = modified[:max_content_length]
            if len(modified) > max_content_length:
                modified_preview += "..."

            context.emit(
                event_type="policy.content_transformed",
                summary=f"Applied {replacements_made} string replacements",
                details={
                    "replacements_count": replacements_made,
                    "original_length": len(content),
                    "modified_length": len(modified),
                    "replacement_rules": len(self.replacements),
                    "original_preview": original_preview,
                    "modified_preview": modified_preview,
                },
            )

            logger.debug(
                f"Applied {replacements_made} string replacements across {len(self.replacements)} rules "
                f"(length {len(content)} -> {len(modified)})"
            )

        return modified

    async def on_response_tool_call(
        self,
        tool_call: ToolCallStreamBlock,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> ToolCallStreamBlock | None:
        """Apply string replacements to tool call if enabled.

        If apply_to_tool_calls is True, applies replacements to:
        - Tool call name
        - Tool call arguments (JSON string)

        Args:
            tool_call: Complete tool call block
            context: Policy context for event emission
            streaming_ctx: Streaming context

        Returns:
            Modified tool call block, or original if apply_to_tool_calls is False
        """
        if not self.apply_to_tool_calls or not self.replacements:
            return tool_call

        replacements_made = 0
        original_name = tool_call.name
        original_args = tool_call.arguments

        # Apply replacements to tool name
        modified_name = tool_call.name
        for before, after in self.replacements.items():
            if before in modified_name:
                count = modified_name.count(before)
                modified_name = modified_name.replace(before, after)
                replacements_made += count

        # Apply replacements to arguments
        modified_args = tool_call.arguments
        for before, after in self.replacements.items():
            if before in modified_args:
                count = modified_args.count(before)
                modified_args = modified_args.replace(before, after)
                replacements_made += count

        # Validate that modified arguments are still valid JSON
        if modified_args != original_args:
            try:
                json.loads(modified_args)
            except json.JSONDecodeError:
                logger.warning(
                    f"String replacements broke JSON in tool call arguments. "
                    f"Reverting to original. Tool: {tool_call.name}"
                )
                modified_args = original_args
                # Recount replacements without the broken args
                replacements_made = 0
                for before, after in self.replacements.items():
                    if before in modified_name:
                        replacements_made += modified_name.count(before)

        # Emit event if any replacements were made
        if replacements_made > 0:
            # Truncate arguments for event emission (avoid huge events)
            max_args_length = 500
            original_args_preview = original_args[:max_args_length]
            if len(original_args) > max_args_length:
                original_args_preview += "..."
            modified_args_preview = modified_args[:max_args_length]
            if len(modified_args) > max_args_length:
                modified_args_preview += "..."

            context.emit(
                event_type="policy.tool_call_transformed",
                summary=f"Applied {replacements_made} string replacements to tool call",
                details={
                    "replacements_count": replacements_made,
                    "tool_name": tool_call.name,
                    "modified_name": modified_name != original_name,
                    "modified_args": modified_args != original_args,
                    "original_name": original_name,
                    "modified_name_value": modified_name,
                    "original_args_preview": original_args_preview,
                    "modified_args_preview": modified_args_preview,
                },
            )

            logger.debug(f"Applied {replacements_made} string replacements to tool call '{tool_call.name}'")

        # Update tool call with modified values
        if modified_name != original_name or modified_args != original_args:
            tool_call.name = modified_name
            tool_call.arguments = modified_args

        return tool_call

    # No need to override:
    # - on_request: passes through unchanged (default)


__all__ = ["SimpleStringReplacementPolicy"]
