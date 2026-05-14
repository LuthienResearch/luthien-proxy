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
from typing import TYPE_CHECKING

from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    ToolUseBlock,
)
from pydantic import BaseModel, Field

from luthien_proxy.policy_core import (
    AnthropicHookPolicy,
    BasePolicy,
    CatalogBadge,
    Category,
    UIMetadata,
)
from luthien_proxy.policy_core.anthropic_message_builder import AnthropicMessageBuilder, BufferedTool

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
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

    # NOTE: ui_policy_preview is a UI hint only. The actual runtime block message
    # is templated with dynamic data (the specific command being blocked).
    ui = UIMetadata(
        display_name="Dogfood Safety",
        short_description="Blocks self-destructive commands during internal dogfooding.",
        category=Category.ACTIVE_MONITORING,
        catalog_badges=(CatalogBadge.BLOCKS,),
        ui_policy_preview="⛔ Blocked: Self-destructive command detected. Blocked to protect the running gateway from being torn down.",
    )

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

    def _decide_tool(
        self,
        tool_name: str,
        tool_input_json: str,
        context: "PolicyContext",
    ) -> tuple[bool, str]:
        """Pattern-match a tool call; emit observability if blocked.

        Returns `(is_blocked, command_preview)`. Fail-secure: if the buffered
        input_json failed to parse, scans the raw text for dangerous patterns
        anyway rather than treating the call as safe.
        """
        try:
            parsed = json.loads(tool_input_json) if tool_input_json else {}
        except json.JSONDecodeError:
            parsed = None
        scan_input: JSONObject | str = parsed if isinstance(parsed, dict) else tool_input_json

        is_blocked, command = self._is_dangerous(tool_name, scan_input)
        if is_blocked:
            context.record_event(
                "policy.dogfood_safety.blocked",
                {"tool_name": tool_name, "command": command[:200]},
            )
            logger.warning(f"Blocked dangerous Anthropic tool_use: {command[:100]}")
        return is_blocked, command

    # ========================================================================
    # Anthropic hooks
    # ========================================================================

    async def on_anthropic_response(
        self, response: "AnthropicResponse", context: "PolicyContext"
    ) -> "AnthropicResponse":
        """Check non-streaming Anthropic tool_use blocks."""
        content = response.get("content") or []
        if not content:
            return response
        # Early-out: nothing to do if there are no tool_use blocks.
        if not any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content):
            return response

        builder = AnthropicMessageBuilder()
        for block in content:
            if not isinstance(block, dict):
                builder.commit_raw_block(block)
                continue
            if block.get("type") == "tool_use":
                tool_input = block.get("input", {})
                input_json = (
                    tool_input if isinstance(tool_input, str) else json.dumps(tool_input) if tool_input else "{}"
                )
                is_blocked, command = self._decide_tool(
                    str(block.get("name", "")),
                    input_json,
                    context,
                )
                if is_blocked:
                    builder.commit_text(self._format_blocked_message(command))
                else:
                    builder.buffer_tool(
                        id=str(block.get("id", "")),
                        name=str(block.get("name", "")),
                        input_json=input_json,
                    )
            elif block.get("type") == "text":
                builder.commit_text(block.get("text", ""))
            else:
                builder.commit_raw_block(block)

        return builder.to_anthropic_response(response)

    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        """Buffer tool_use blocks in streaming, evaluate on completion."""
        builder = context.get_request_state(self, AnthropicMessageBuilder, AnthropicMessageBuilder)

        if isinstance(event, RawContentBlockStartEvent):
            cb = event.content_block
            if isinstance(cb, ToolUseBlock):
                builder.begin_tool_buffer(event.index, id=cb.id, name=cb.name)
                return []
            return builder.passthrough_start(event)

        if isinstance(event, RawContentBlockDeltaEvent):
            if isinstance(event.delta, InputJSONDelta) and builder.append_tool_delta(
                event.index, event.delta.partial_json
            ):
                return []
            return builder.passthrough_delta(event)

        if isinstance(event, RawContentBlockStopEvent):
            tool = builder.take_tool(event.index)
            if tool is not None:
                return self._handle_tool_stop(builder, tool, context)
            return builder.passthrough_stop(event)

        if isinstance(event, RawMessageDeltaEvent):
            return builder.finalize(event)

        return [event]

    def _handle_tool_stop(
        self,
        builder: AnthropicMessageBuilder,
        tool: BufferedTool,
        context: "PolicyContext",
    ) -> list[MessageStreamEvent]:
        """Apply pattern-match decision at tool block_stop."""
        is_blocked, command = self._decide_tool(tool.name, tool.input_json, context)
        if is_blocked:
            return builder.commit_text(self._format_blocked_message(command))
        builder.buffer_tool(id=tool.id, name=tool.name, input_json=tool.input_json)
        return []

    async def on_anthropic_streaming_policy_complete(self, context: "PolicyContext") -> None:
        """Clean up request-scoped Anthropic state."""
        context.pop_request_state(self, AnthropicMessageBuilder)


__all__ = ["DogfoodSafetyPolicy", "DogfoodSafetyConfig"]
