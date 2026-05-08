"""DogfoodSafetyPolicy - Block self-destructive commands during dogfooding.

When an AI agent runs through the Luthien proxy, it can accidentally kill
the proxy by running commands like `docker compose down`. This policy
pattern-matches tool calls against a blocklist and blocks dangerous commands.

Unlike ToolCallJudgePolicy (which calls an external LLM), this uses fast
regex matching for zero-latency, deterministic blocking.

Auto-composed via DOGFOOD_MODE=true — wraps whatever policy is configured.

Example config:
    policy:
      class: "luthien_proxy.policies.dogfood_safety_policy:DogfoodSafetyPolicy"
      config:
        tool_names: ["Bash", "bash", "shell"]
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
)
from pydantic import BaseModel, Field

from luthien_proxy.policies.tool_call_judge_utils import (
    BufferedToolUse,
    build_allowed_tool_use_events,
    build_blocked_non_streaming_response,
    build_blocked_text_events,
    handle_tool_use_block_delta,
    handle_tool_use_block_start,
)
from luthien_proxy.policy_core import (
    AnthropicHookPolicy,
    BasePolicy,
)

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicContentBlock,
        AnthropicResponse,
        JSONObject,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)

DEFAULT_DANGEROUS_PATTERNS = [
    # Docker commands that stop/kill containers
    r"docker\s+compose\s+(down|stop|rm|kill)",
    r"docker-compose\s+(down|stop|rm|kill)",
    r"docker\s+(stop|kill|rm)\s",
    # Process killing targeting proxy processes
    r"pkill\s+.*(uvicorn|python|luthien|gateway)",
    r"killall\s+.*(uvicorn|python|luthien)",
    # Destructive file operations on proxy infrastructure
    r"rm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?(\.env|docker-compose|src/luthien)",
    # Database destruction via docker exec
    r"docker\s+compose\s+exec.*psql.*DROP\s",
    r"docker\s+compose\s+exec.*psql.*TRUNCATE\s",
]

DEFAULT_TOOL_NAMES = ["Bash", "bash", "shell", "terminal", "execute", "run_command"]


@dataclass
class _DogfoodAnthropicState:
    buffered_tool_uses: dict[int, BufferedToolUse] = field(default_factory=dict)


class DogfoodSafetyConfig(BaseModel):
    """Configuration for DogfoodSafetyPolicy."""

    blocked_patterns: list[str] = Field(
        default_factory=lambda: list(DEFAULT_DANGEROUS_PATTERNS),
        description="Regex patterns to block in bash tool call arguments",
    )
    tool_names: list[str] = Field(
        default_factory=lambda: list(DEFAULT_TOOL_NAMES),
        description="Tool names considered bash/shell executors",
    )
    blocked_message: str = Field(
        default=(
            "⛔ BLOCKED by DogfoodSafetyPolicy: '{command}' would disrupt "
            "the Luthien proxy infrastructure. Use a separate terminal for "
            "Docker/infrastructure commands while dogfooding."
        ),
        description="Message template. Variables: {command}, {pattern}",
    )


class DogfoodSafetyPolicy(BasePolicy, AnthropicHookPolicy):
    """Fast pattern-matching policy that blocks self-destructive commands.

    Protects the proxy from being killed by the agent running through it.
    Uses pure regex — zero latency, no LLM dependency, deterministic.
    """

    @property
    def short_policy_name(self) -> str:
        """Policy display name."""
        return "DogfoodSafety"

    def __init__(self, config: DogfoodSafetyConfig | None = None):
        """Initialize with optional config for blocked patterns and tool names."""
        self.config = self._init_config(config, DogfoodSafetyConfig)
        self._compiled_patterns = tuple(re.compile(p, re.IGNORECASE) for p in self.config.blocked_patterns)
        self._tool_names_lower = frozenset(n.lower() for n in self.config.tool_names)

        logger.info(
            f"DogfoodSafetyPolicy initialized: "
            f"{len(self._compiled_patterns)} patterns, "
            f"tool_names={self.config.tool_names}"
        )

    def _anthropic_state(self, context: "PolicyContext") -> _DogfoodAnthropicState:
        """Get or create request-scoped Anthropic streaming state."""
        return context.get_request_state(self, _DogfoodAnthropicState, _DogfoodAnthropicState)

    def _anthropic_buffered_tool_uses(self, context: "PolicyContext") -> dict[int, BufferedToolUse]:
        return self._anthropic_state(context).buffered_tool_uses

    # ========================================================================
    # Core matching logic
    # ========================================================================

    def _is_dangerous(self, tool_name: str, tool_input: "JSONObject | str") -> tuple[bool, str]:
        """Check if a tool call contains a dangerous command.

        Returns (is_blocked, command_string).
        """
        if tool_name.lower() not in self._tool_names_lower:
            return False, ""

        command = self._extract_command(tool_input)
        if not command:
            return False, ""

        for pattern in self._compiled_patterns:
            if pattern.search(command):
                return True, command

        return False, ""

    def _extract_command(self, tool_input: "JSONObject | str") -> str:
        """Extract command string from tool input (handles Claude Code's format)."""
        if isinstance(tool_input, str):
            try:
                parsed = json.loads(tool_input)
                if isinstance(parsed, dict):
                    return str(parsed.get("command", ""))
            except (json.JSONDecodeError, TypeError) as e:
                logger.debug(f"Could not parse tool_input as JSON in _extract_command: {repr(e)}")
                return tool_input
        elif isinstance(tool_input, dict):
            return str(tool_input.get("command", ""))
        return ""

    def _format_blocked_message(self, command: str) -> str:
        """Render blocked-message template with truncated command."""
        return self.config.blocked_message.format(command=command[:200], pattern="regex")

    # ========================================================================
    # Anthropic hooks (via AnthropicHookPolicy)
    # ========================================================================

    async def on_anthropic_response(
        self, response: "AnthropicResponse", context: "PolicyContext"
    ) -> "AnthropicResponse":
        """Check non-streaming Anthropic tool_use blocks."""
        content = response.get("content", [])
        if not content:
            return response

        new_content: list[AnthropicContentBlock] = []
        modified = False

        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = str(block.get("name", ""))
                input_data = block.get("input", {})
                is_blocked, command = self._is_dangerous(name, input_data)

                if is_blocked:
                    msg = self._format_blocked_message(command)
                    new_content.append({"type": "text", "text": msg})
                    modified = True
                    context.record_event(
                        "policy.dogfood_safety.blocked",
                        {"tool_name": name, "command": command[:200]},
                    )
                    logger.warning(f"Blocked dangerous Anthropic tool_use: {command[:100]}")
                else:
                    new_content.append(block)
            else:
                new_content.append(block)

        if modified:
            return build_blocked_non_streaming_response(response, new_content)

        return response

    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        """Buffer tool_use blocks in streaming, evaluate on completion."""
        buffered_tool_uses = self._anthropic_buffered_tool_uses(context)

        if isinstance(event, RawContentBlockStartEvent):
            return handle_tool_use_block_start(event, buffered_tool_uses)

        if isinstance(event, RawContentBlockDeltaEvent):
            return handle_tool_use_block_delta(event, buffered_tool_uses)

        if isinstance(event, RawContentBlockStopEvent):
            if event.index not in buffered_tool_uses:
                return [cast(MessageStreamEvent, event)]

            buffered = buffered_tool_uses.pop(event.index)
            is_blocked, command = self._is_dangerous(buffered.name, buffered.input_json)

            if is_blocked:
                msg = self._format_blocked_message(command)
                context.record_event(
                    "policy.dogfood_safety.blocked",
                    {"tool_name": buffered.name, "command": command[:200]},
                )
                logger.warning(f"Blocked dangerous Anthropic streaming tool_use: {command[:100]}")
                return build_blocked_text_events(event.index, event, msg)

            return build_allowed_tool_use_events(buffered, event)

        return [event]

    async def on_anthropic_streaming_policy_complete(self, context: "PolicyContext") -> None:
        """Clean up request-scoped Anthropic state."""
        context.pop_request_state(self, _DogfoodAnthropicState)


__all__ = ["DogfoodSafetyPolicy", "DogfoodSafetyConfig"]
