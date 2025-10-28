# ABOUTME: SimpleEventBasedPolicy that blocks content matching regex patterns
# ABOUTME: Replaces blocked content and tool calls with configurable message

r"""Simple blocked content policy using regex pattern matching.

This policy blocks content and tool calls that match configured regex patterns,
replacing them with a configurable message. It's useful for:
- Content filtering and moderation
- Blocking sensitive information patterns (SSNs, credit cards, etc.)
- Preventing specific tool calls based on name or argument patterns
- Testing content blocking in e2e tests

The policy compiles all patterns once at initialization and applies them to:
- Response content (text)
- Tool call names and arguments (when matched)

Usage in config:
    policy:
      class: "luthien_proxy.v2.policies.simple_blocked_content:SimpleBlockedContentPolicy"
      config:
        blocked_patterns:
          - "\\b\\d{3}-\\d{2}-\\d{4}\\b"  # SSN pattern
          - "password"
          - "secret"
        replacement_message: "[BLOCKED: Sensitive content detected]"
        block_tool_calls: true  # Optional, default is true
"""

from __future__ import annotations

import logging
import re

from luthien_proxy.v2.policies.policy_context import PolicyContext
from luthien_proxy.v2.policies.simple_event_based_policy import (
    SimpleEventBasedPolicy,
    StreamingContext,
)
from luthien_proxy.v2.streaming.stream_blocks import ToolCallStreamBlock

logger = logging.getLogger(__name__)


class SimpleBlockedContentPolicy(SimpleEventBasedPolicy):
    r"""Policy that blocks content and tool calls matching regex patterns.

    Scans response content and optionally tool calls for patterns that should be blocked.
    When a match is found, replaces the content with a configurable message.

    For tool calls, if the tool name or arguments match a blocked pattern, the tool call
    is replaced with text content containing the replacement message.

    Config:
        blocked_patterns: List of regex patterns to detect blocked content
        replacement_message: Message to send in place of blocked content
        block_tool_calls: If True (default), also check tool calls for blocked patterns

    Example:
        blocked_patterns:
          - "\\b\\d{3}-\\d{2}-\\d{4}\\b"  # SSN
          - "\\b\\d{16}\\b"  # Credit card
          - "password"
          - "execute_code"
        replacement_message: "[BLOCKED: Sensitive content detected]"
        block_tool_calls: true
    """

    def __init__(
        self,
        blocked_patterns: list[str] | None = None,
        replacement_message: str = "[BLOCKED: Content policy violation]",
        block_tool_calls: bool = True,
    ):
        """Initialize policy with blocked patterns and replacement message.

        Args:
            blocked_patterns: List of regex patterns (strings) to detect blocked content.
                            Patterns are compiled with re.IGNORECASE flag.
                            Defaults to empty list (no blocking).
            replacement_message: Message to send when content is blocked.
                                Defaults to "[BLOCKED: Content policy violation]".
            block_tool_calls: If True, also check tool calls for blocked patterns.
                             If a tool call matches, it's replaced with text content.
                             Defaults to True.
        """
        super().__init__()
        self.blocked_patterns = blocked_patterns or []
        self.replacement_message = replacement_message
        self.block_tool_calls = block_tool_calls

        # Compile regex patterns once at initialization
        self.compiled_patterns: list[re.Pattern] = []
        for pattern in self.blocked_patterns:
            try:
                compiled = re.compile(pattern, re.IGNORECASE)
                self.compiled_patterns.append(compiled)
            except re.error as e:
                logger.error(f"Invalid regex pattern '{pattern}': {e}")
                raise ValueError(f"Invalid regex pattern '{pattern}': {e}") from e

        if not self.blocked_patterns:
            logger.warning("SimpleBlockedContentPolicy initialized with no blocked patterns")
        else:
            logger.info(
                f"SimpleBlockedContentPolicy initialized with {len(self.blocked_patterns)} patterns "
                f"(block_tool_calls={block_tool_calls})"
            )

    def _matches_blocked_pattern(self, text: str) -> tuple[bool, str | None]:
        """Check if text matches any blocked pattern.

        Args:
            text: Text to check against patterns

        Returns:
            Tuple of (matches, matched_pattern_string).
            If matches is True, matched_pattern_string contains the pattern that matched.
        """
        for pattern, compiled in zip(self.blocked_patterns, self.compiled_patterns, strict=False):
            if compiled.search(text):
                return True, pattern
        return False, None

    async def on_content_simple(
        self,
        content: str,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> str:
        """Check content for blocked patterns and replace if found.

        Args:
            content: Original content text
            context: Policy context for event emission
            streaming_ctx: Streaming context

        Returns:
            Original content if no patterns match, otherwise replacement message
        """
        if not self.compiled_patterns:
            return content

        matches, matched_pattern = self._matches_blocked_pattern(content)

        if matches:
            # Truncate content for event emission (avoid huge events)
            max_content_length = 200
            content_preview = content[:max_content_length]
            if len(content) > max_content_length:
                content_preview += "..."

            context.emit(
                event_type="policy.content_blocked",
                summary=f"Blocked content matching pattern: {matched_pattern}",
                details={
                    "matched_pattern": matched_pattern,
                    "original_length": len(content),
                    "replacement_message": self.replacement_message,
                    "content_preview": content_preview,
                    "policy_class": self.__class__.__name__,
                },
                severity="warning",
            )

            logger.warning(
                f"Blocked content matching pattern '{matched_pattern}' "
                f"(length={len(content)}, replaced with: '{self.replacement_message}')"
            )

            return self.replacement_message

        return content

    async def on_tool_call_simple(
        self,
        tool_call: ToolCallStreamBlock,
        context: PolicyContext,
        streaming_ctx: StreamingContext,
    ) -> ToolCallStreamBlock | str | None:
        """Check tool call for blocked patterns and replace if found.

        If block_tool_calls is True, checks both the tool name and arguments
        for blocked patterns. If a match is found, replaces the tool call with
        text content containing the replacement message.

        Args:
            tool_call: Complete tool call block
            context: Policy context for event emission
            streaming_ctx: Streaming context

        Returns:
            - Original tool call if no patterns match or block_tool_calls is False
            - String (replacement message) if tool call matches blocked pattern
        """
        if not self.block_tool_calls or not self.compiled_patterns:
            return tool_call

        # Check tool name
        name_matches, name_pattern = self._matches_blocked_pattern(tool_call.name)

        # Check tool arguments
        args_matches, args_pattern = self._matches_blocked_pattern(tool_call.arguments)

        if name_matches or args_matches:
            matched_pattern = name_pattern if name_matches else args_pattern
            matched_field = "name" if name_matches else "arguments"

            # Truncate arguments for event emission (avoid huge events)
            max_args_length = 200
            args_preview = tool_call.arguments[:max_args_length]
            if len(tool_call.arguments) > max_args_length:
                args_preview += "..."

            context.emit(
                event_type="policy.tool_call_blocked",
                summary=f"Blocked tool call '{tool_call.name}' matching pattern: {matched_pattern}",
                details={
                    "tool_name": tool_call.name,
                    "tool_id": tool_call.id,
                    "matched_pattern": matched_pattern,
                    "matched_field": matched_field,
                    "replacement_message": self.replacement_message,
                    "args_preview": args_preview,
                    "policy_class": self.__class__.__name__,
                },
                severity="warning",
            )

            logger.warning(
                f"Blocked tool call '{tool_call.name}' (id={tool_call.id}) "
                f"matching pattern '{matched_pattern}' in {matched_field}"
            )

            # Return string to replace tool call with text content
            return self.replacement_message

        # Allow tool call (pass through)
        return tool_call


__all__ = ["SimpleBlockedContentPolicy"]
