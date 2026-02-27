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
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    TextBlock,
    TextDelta,
    ToolUseBlock,
)
from litellm.types.utils import (
    ChatCompletionMessageToolCall,
    Function,
    StreamingChoices,
)
from pydantic import BaseModel, Field

from luthien_proxy.policy_core import (
    AnthropicExecutionInterface,
    AnthropicPolicyEmission,
    AnthropicPolicyIOProtocol,
    BasePolicy,
    OpenAIPolicyInterface,
    create_finish_chunk,
    create_text_chunk,
    create_text_response,
    create_tool_call_chunk,
    extract_tool_calls_from_response,
)
from luthien_proxy.streaming.stream_blocks import ToolCallStreamBlock

if TYPE_CHECKING:
    from litellm.types.utils import ModelResponse

    from luthien_proxy.llm.types import Request
    from luthien_proxy.llm.types.anthropic import (
        AnthropicRequest,
        AnthropicResponse,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext

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
class _BufferedOpenAIToolCall:
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class _DogfoodOpenAIState:
    buffered_tool_calls: dict[int, _BufferedOpenAIToolCall] = field(default_factory=dict)
    blocked: bool = False


@dataclass
class _BufferedAnthropicToolUse:
    id: str
    name: str
    input_json: str = ""


@dataclass
class _DogfoodAnthropicState:
    buffered_tool_uses: dict[int, _BufferedAnthropicToolUse] = field(default_factory=dict)


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


class DogfoodSafetyPolicy(BasePolicy, OpenAIPolicyInterface, AnthropicExecutionInterface):
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
        self._compiled_patterns = [re.compile(p, re.IGNORECASE) for p in self.config.blocked_patterns]
        self._tool_names_lower = {n.lower() for n in self.config.tool_names}

        logger.info(
            f"DogfoodSafetyPolicy initialized: "
            f"{len(self._compiled_patterns)} patterns, "
            f"tool_names={self.config.tool_names}"
        )

    def _openai_state(self, ctx: "StreamingPolicyContext") -> _DogfoodOpenAIState:
        """Get or create request-scoped OpenAI streaming state."""
        return ctx.policy_ctx.get_policy_state(self, _DogfoodOpenAIState, _DogfoodOpenAIState)

    def _openai_buffered_tool_calls(self, ctx: "StreamingPolicyContext") -> dict[int, _BufferedOpenAIToolCall]:
        """Get request-scoped OpenAI tool-call buffers."""
        return self._openai_state(ctx).buffered_tool_calls

    def _openai_is_blocked(self, ctx: "StreamingPolicyContext") -> bool:
        """Whether this OpenAI streaming request has already been blocked."""
        return self._openai_state(ctx).blocked

    def _openai_mark_blocked(self, ctx: "StreamingPolicyContext") -> None:
        """Mark current OpenAI streaming request as blocked."""
        self._openai_state(ctx).blocked = True

    def _anthropic_state(self, context: "PolicyContext") -> _DogfoodAnthropicState:
        """Get or create request-scoped Anthropic streaming state."""
        return context.get_policy_state(self, _DogfoodAnthropicState, _DogfoodAnthropicState)

    def _anthropic_buffered_tool_uses(self, context: "PolicyContext") -> dict[int, _BufferedAnthropicToolUse]:
        """Get request-scoped Anthropic tool_use buffers."""
        return self._anthropic_state(context).buffered_tool_uses

    # ========================================================================
    # Core matching logic
    # ========================================================================

    def _is_dangerous(self, tool_name: str, tool_input: dict[str, Any] | str) -> tuple[bool, str]:
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

    def _extract_command(self, tool_input: dict[str, Any] | str) -> str:
        """Extract command string from tool input (handles Claude Code's format)."""
        if isinstance(tool_input, str):
            try:
                parsed = json.loads(tool_input)
                if isinstance(parsed, dict):
                    return str(parsed.get("command", ""))
            except (json.JSONDecodeError, TypeError):
                return tool_input
        elif isinstance(tool_input, dict):
            return str(tool_input.get("command", ""))
        return ""

    def _format_blocked_message(self, command: str) -> str:
        """Render blocked-message template with truncated command."""
        return self.config.blocked_message.format(command=command[:200], pattern="regex")

    # ========================================================================
    # OpenAI Interface
    # ========================================================================

    async def on_openai_request(self, request: "Request", context: "PolicyContext") -> "Request":
        """Pass requests through unmodified."""
        return request

    async def on_openai_response(self, response: "ModelResponse", context: "PolicyContext") -> "ModelResponse":
        """Check non-streaming tool calls and block if dangerous."""
        tool_calls = extract_tool_calls_from_response(response)
        if not tool_calls:
            return response

        for tc in tool_calls:
            name = str(tc.get("name", ""))
            args = tc.get("arguments", "{}")
            is_blocked, command = self._is_dangerous(name, args)
            if is_blocked:
                msg = self._format_blocked_message(command)
                context.record_event(
                    "policy.dogfood_safety.blocked",
                    {"tool_name": name, "command": command[:200]},
                )
                logger.warning(f"Blocked dangerous command in non-streaming: {command[:100]}")
                return create_text_response(msg)

        return response

    async def on_chunk_received(self, ctx: "StreamingPolicyContext") -> None:
        """No-op — handled by specific delta hooks."""
        pass

    async def on_content_delta(self, ctx: "StreamingPolicyContext") -> None:
        """Forward content deltas as-is."""
        current_chunk = ctx.original_streaming_response_state.raw_chunks[-1]
        ctx.egress_queue.put_nowait(current_chunk)

    async def on_content_complete(self, ctx: "StreamingPolicyContext") -> None:
        """No-op — content passes through without modification."""
        pass

    async def on_tool_call_delta(self, ctx: "StreamingPolicyContext") -> None:
        """Buffer tool call deltas for later evaluation."""
        if not ctx.original_streaming_response_state.raw_chunks:
            return

        current_chunk = ctx.original_streaming_response_state.raw_chunks[-1]
        if not current_chunk.choices:
            return

        choice = cast(StreamingChoices, current_chunk.choices[0])
        delta = choice.delta

        if not hasattr(delta, "tool_calls") or not delta.tool_calls:
            return

        buffered_tool_calls = self._openai_buffered_tool_calls(ctx)
        for tc_delta in delta.tool_calls:
            tc_index = tc_delta.index if hasattr(tc_delta, "index") else 0
            if tc_index not in buffered_tool_calls:
                buffered_tool_calls[tc_index] = _BufferedOpenAIToolCall()

            buffer = buffered_tool_calls[tc_index]
            if hasattr(tc_delta, "id") and tc_delta.id:
                buffer.id = tc_delta.id
            if hasattr(tc_delta, "function"):
                func = tc_delta.function
                if hasattr(func, "name") and func.name:
                    buffer.name += func.name
                if hasattr(func, "arguments") and func.arguments:
                    buffer.arguments += func.arguments

        delta.tool_calls = None

    async def on_tool_call_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Check completed tool call against patterns."""
        if self._openai_is_blocked(ctx):
            return

        just_completed = ctx.original_streaming_response_state.just_completed
        if not just_completed or not isinstance(just_completed, ToolCallStreamBlock):
            return

        buffered_tool_calls = self._openai_buffered_tool_calls(ctx)
        if just_completed.index not in buffered_tool_calls:
            return

        tool_call = buffered_tool_calls[just_completed.index]
        is_blocked, command = self._is_dangerous(tool_call.name, tool_call.arguments)

        if is_blocked:
            self._openai_mark_blocked(ctx)
            msg = self._format_blocked_message(command)
            ctx.policy_ctx.record_event(
                "policy.dogfood_safety.blocked",
                {"tool_name": tool_call.name, "command": command[:200]},
            )
            logger.warning(f"Blocked dangerous command in streaming: {command[:100]}")

            await ctx.egress_queue.put(create_text_chunk(msg, finish_reason=None))
            await ctx.egress_queue.put(create_text_chunk("", finish_reason="stop"))
            return

        tc_obj = ChatCompletionMessageToolCall(
            id=tool_call.id,
            function=Function(
                name=tool_call.name,
                arguments=tool_call.arguments,
            ),
        )
        await ctx.egress_queue.put(create_tool_call_chunk(tc_obj))

    async def on_finish_reason(self, ctx: "StreamingPolicyContext") -> None:
        """No-op — finish reason emitted in on_stream_complete."""
        pass

    async def on_stream_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Emit finish_reason for allowed tool call responses."""
        finish_reason = ctx.original_streaming_response_state.finish_reason
        if not finish_reason:
            return

        if self._openai_is_blocked(ctx):
            return

        blocks = ctx.original_streaming_response_state.blocks
        has_tool_calls = any(isinstance(block, ToolCallStreamBlock) for block in blocks)
        if not has_tool_calls:
            return

        raw_chunks = ctx.original_streaming_response_state.raw_chunks
        last_chunk = raw_chunks[-1] if raw_chunks else None
        finish_chunk = create_finish_chunk(
            finish_reason=finish_reason,
            model=last_chunk.model if last_chunk else "luthien-policy",
            chunk_id=last_chunk.id if last_chunk else None,
        )
        await ctx.egress_queue.put(finish_chunk)

    async def on_streaming_policy_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Clean up request-scoped OpenAI state."""
        ctx.policy_ctx.pop_policy_state(self, _DogfoodOpenAIState)

    # ========================================================================
    # Anthropic execution interface
    # ========================================================================

    def run_anthropic(
        self, io: AnthropicPolicyIOProtocol, context: "PolicyContext"
    ) -> AsyncIterator[AnthropicPolicyEmission]:
        """Run Anthropic request lifecycle for dogfood safety filtering."""

        async def _run() -> AsyncIterator[AnthropicPolicyEmission]:
            final_request = await self.on_anthropic_request(io.request, context)
            io.set_request(final_request)

            if final_request.get("stream", False):
                async for event in io.stream(final_request):
                    emitted_events = await self.on_anthropic_stream_event(event, context)
                    for emitted_event in emitted_events:
                        yield emitted_event
                return

            response = await io.complete(final_request)
            yield await self.on_anthropic_response(response, context)

        return _run()

    async def on_anthropic_request(self, request: "AnthropicRequest", context: "PolicyContext") -> "AnthropicRequest":
        """Pass requests through unmodified."""
        return request

    async def on_anthropic_response(
        self, response: "AnthropicResponse", context: "PolicyContext"
    ) -> "AnthropicResponse":
        """Check non-streaming Anthropic tool_use blocks."""
        content = response.get("content", [])
        if not content:
            return response

        new_content: list[Any] = []
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
            modified_response = dict(response)
            modified_response["content"] = new_content
            has_tool_use = any(isinstance(b, dict) and b.get("type") == "tool_use" for b in new_content)
            if not has_tool_use and modified_response.get("stop_reason") == "tool_use":
                modified_response["stop_reason"] = "end_turn"
            return cast("AnthropicResponse", modified_response)

        return response

    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        """Buffer tool_use blocks in streaming, evaluate on completion."""
        buffered_tool_uses = self._anthropic_buffered_tool_uses(context)

        if isinstance(event, RawContentBlockStartEvent):
            if isinstance(event.content_block, ToolUseBlock):
                buffered_tool_uses[event.index] = _BufferedAnthropicToolUse(
                    id=event.content_block.id,
                    name=event.content_block.name,
                )
                return []
            return [event]

        if isinstance(event, RawContentBlockDeltaEvent):
            if event.index in buffered_tool_uses and isinstance(event.delta, InputJSONDelta):
                buffered_tool_uses[event.index].input_json += event.delta.partial_json
                return []
            return [event]

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

                text_block = TextBlock(type="text", text="")
                start_event = RawContentBlockStartEvent(
                    type="content_block_start",
                    index=event.index,
                    content_block=text_block,
                )
                delta_event = RawContentBlockDeltaEvent(
                    type="content_block_delta",
                    index=event.index,
                    delta=TextDelta(type="text_delta", text=msg),
                )
                return [
                    cast(MessageStreamEvent, start_event),
                    cast(MessageStreamEvent, delta_event),
                    cast(MessageStreamEvent, event),
                ]

            tool_use_block = ToolUseBlock(
                type="tool_use",
                id=buffered.id,
                name=buffered.name,
                input={},
            )
            start_event = RawContentBlockStartEvent(
                type="content_block_start",
                index=event.index,
                content_block=tool_use_block,
            )
            delta_event = RawContentBlockDeltaEvent(
                type="content_block_delta",
                index=event.index,
                delta=InputJSONDelta(type="input_json_delta", partial_json=buffered.input_json or "{}"),
            )
            return [
                cast(MessageStreamEvent, start_event),
                cast(MessageStreamEvent, delta_event),
                cast(MessageStreamEvent, event),
            ]

        return [event]

    async def on_anthropic_stream_complete(self, context: "PolicyContext") -> None:
        """No-op hook for parity with OpenAI lifecycle."""
        pass

    async def on_anthropic_streaming_policy_complete(self, context: "PolicyContext") -> None:
        """Clean up request-scoped Anthropic state."""
        context.pop_policy_state(self, _DogfoodAnthropicState)


__all__ = ["DogfoodSafetyPolicy", "DogfoodSafetyConfig"]
