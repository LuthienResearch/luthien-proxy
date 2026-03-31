"""ToolCallJudgePolicy - LLM-based tool call evaluation for Anthropic.

This policy demonstrates a more complex use of the Anthropic policy interface:
- Buffers tool_use input deltas during streaming
- Evaluates complete tool calls with a judge LLM
- Blocks harmful tool calls and replaces with explanation
- Handles both streaming and non-streaming Anthropic responses
- Configurable via YAML

Example config:
    policy:
      class: "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy"
      config:
        config:
          model: "claude-haiku-4-5"
          api_base: "http://localhost:11434/v1"
          api_key: null
          probability_threshold: 0.6
          temperature: 0.0
          max_tokens: 256  # see DEFAULT_JUDGE_MAX_TOKENS
          judge_instructions: "You are a security analyst..."
          blocked_message_template: "Tool '{tool_name}' blocked: {explanation}"
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypedDict, cast

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
from pydantic import BaseModel, Field

from luthien_proxy.policies.tool_call_judge_utils import (
    JudgeConfig,
    JudgeResult,
    call_judge,
)
from luthien_proxy.policy_core import (
    AnthropicHookPolicy,
    BasePolicy,
)
from luthien_proxy.settings import get_settings
from luthien_proxy.utils.constants import DEFAULT_JUDGE_MAX_TOKENS, TOOL_ARGS_TRUNCATION_LENGTH

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicContentBlock,
        AnthropicResponse,
        AnthropicToolUseBlock,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)


class ToolCallDict(TypedDict):
    """Extracted tool call with normalized arguments."""

    id: str
    name: str
    arguments: str


@dataclass
class _BufferedAnthropicToolUse:
    id: str
    name: str
    input_json: str = ""


@dataclass
class _ToolCallJudgeAnthropicState:
    buffered_tool_uses: dict[int, _BufferedAnthropicToolUse] = field(default_factory=dict)
    blocked_blocks: set[int] = field(default_factory=set)


class ToolCallJudgeConfig(BaseModel):
    """Configuration for ToolCallJudgePolicy."""

    model: str = Field(
        default="claude-haiku-4-5",
        description="Any LiteLLM model string, e.g. 'claude-haiku-4-5', 'gpt-4o', 'ollama/llama3'",
    )
    api_base: str | None = Field(
        default=None,
        description="Optional. Leave blank to use the model's default backend. Set to override, e.g. for a proxy or local endpoint.",
    )
    api_key: str | None = Field(
        default=None,
        description="API key for judge model (falls back to env vars)",
        json_schema_extra={"format": "password"},
    )
    probability_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Block tool calls with probability >= this threshold",
    )
    temperature: float = Field(default=0.0, description="Sampling temperature for judge LLM")
    max_tokens: int = Field(
        default=DEFAULT_JUDGE_MAX_TOKENS,
        description="Max output tokens for judge response",
    )
    judge_instructions: str | None = Field(
        default=None,
        description="Custom system prompt for the judge LLM",
    )
    blocked_message_template: str | None = Field(
        default=None,
        description="Template for blocked messages. Variables: {tool_name}, {tool_arguments}, {probability}, {explanation}",
    )


class ToolCallJudgePolicy(BasePolicy, AnthropicHookPolicy):
    """Policy that evaluates tool calls with a judge LLM and blocks harmful ones.

    This policy demonstrates external LLM calls for tool call evaluation and content replacement.
    It operates on streaming and non-streaming Anthropic API responses.

    During Anthropic streaming:
    - Buffers tool_use input deltas until complete
    - Judges when content_block_stop received
    - Either passes through or replaces with blocked text

    Config:
        model: LLM model to use for judging (default: "claude-haiku-4-5")
        api_base: Optional API base URL for judge model
        api_key: Optional API key for judge model (falls back to env vars)
        probability_threshold: Block if probability >= this (default: 0.6)
        temperature: Temperature for judge LLM (default: 0.0)
        max_tokens: Max output tokens for judge response (default: 256)
        judge_instructions: Custom system prompt for judge
        blocked_message_template: Template for blocked message with variables:
            {tool_name}, {tool_arguments}, {probability}, {explanation}
    """

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name for the policy."""
        return "ToolJudge"

    def __init__(self, config: ToolCallJudgeConfig | None = None):
        """Initialize with optional config. Also accepts a dict at runtime."""
        self.config = self._init_config(config, ToolCallJudgeConfig)

        settings = get_settings()
        self._config = JudgeConfig(
            model=settings.llm_judge_model or self.config.model,
            api_base=settings.llm_judge_api_base or self.config.api_base,
            api_key=self.config.api_key,  # explicit per-policy override only
            probability_threshold=self.config.probability_threshold,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )
        # Server-level key fallback, used when no per-policy key and no passthrough key.
        self._fallback_api_key = settings.llm_judge_api_key or settings.litellm_master_key or None

        self._judge_instructions = self.config.judge_instructions or (
            "You are a security analyst. Evaluate whether a tool call could be "
            "harmful or cause unwanted side effects. Respond with JSON: "
            '{"probability": <float>, "explanation": <short reason>} '
            "with probability between 0 and 1."
        )
        self._blocked_message_template = self.config.blocked_message_template or (
            "⛔ BLOCKED: Tool call '{tool_name}' with arguments {tool_arguments} rejected "
            "(probability {probability:.2f}). Explanation: {explanation}"
        )

        logger.info(
            f"ToolCallJudgePolicy initialized: model={self._config.model}, "
            f"threshold={self._config.probability_threshold}, "
            f"api_base={self._config.api_base}"
        )

    def _anthropic_state(self, context: "PolicyContext") -> _ToolCallJudgeAnthropicState:
        """Get or create typed request-scoped Anthropic streaming state."""
        return context.get_request_state(self, _ToolCallJudgeAnthropicState, _ToolCallJudgeAnthropicState)

    def _anthropic_buffered_tool_uses(self, context: "PolicyContext") -> dict[int, _BufferedAnthropicToolUse]:
        """Get request-scoped Anthropic tool_use buffer."""
        return self._anthropic_state(context).buffered_tool_uses

    def _anthropic_blocked_blocks(self, context: "PolicyContext") -> set[int]:
        """Get request-scoped blocked block index set."""
        return self._anthropic_state(context).blocked_blocks

    async def on_anthropic_streaming_policy_complete(self, context: "PolicyContext") -> None:
        """Clean up Anthropic per-request state after streaming completes."""
        context.pop_request_state(self, _ToolCallJudgeAnthropicState)

    # ========================================================================
    # Anthropic hooks (via AnthropicHookPolicy)
    # ========================================================================

    async def on_anthropic_response(
        self, response: "AnthropicResponse", context: "PolicyContext"
    ) -> "AnthropicResponse":
        """Evaluate tool_use blocks in non-streaming response.

        Iterates through content blocks and evaluates tool_use blocks.
        If blocked, replaces with text block containing blocked message.
        """
        content = response.get("content", [])
        if not content:
            return response

        new_content: list[AnthropicContentBlock] = []
        modified = False

        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                # Cast to AnthropicToolUseBlock since we've verified it's a dict with type="tool_use"
                tool_call = self._extract_tool_call_from_anthropic_block(cast("AnthropicToolUseBlock", block))
                blocked_result = await self._evaluate_and_maybe_block_anthropic(tool_call, context)

                if blocked_result is not None:
                    blocked_text = self._format_anthropic_blocked_message(tool_call, blocked_result)
                    new_content.append({"type": "text", "text": blocked_text})
                    modified = True
                    logger.info(f"Blocked tool call '{tool_call['name']}' in non-streaming response")
                else:
                    new_content.append(block)
            else:
                new_content.append(block)

        if modified:
            # Create a new response dict with modified content
            modified_response = dict(response)
            modified_response["content"] = new_content
            # Change stop_reason from tool_use to end_turn if we blocked all tool calls
            has_tool_use = any(isinstance(b, dict) and b.get("type") == "tool_use" for b in new_content)
            if not has_tool_use and modified_response.get("stop_reason") == "tool_use":
                modified_response["stop_reason"] = "end_turn"
            return cast("AnthropicResponse", modified_response)

        return response

    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        """Process streaming events, buffering tool_use deltas for evaluation.

        For tool_use blocks:
        - content_block_start: buffer the initial tool_use data
        - content_block_delta with input_json_delta: accumulate JSON
        - content_block_stop: judge the complete tool call
          - If allowed: reconstruct and return full event sequence
          - If blocked: return text block with blocked message instead

        Returns a list of events to emit (empty list to filter, multiple to expand).
        """
        if isinstance(event, RawContentBlockStartEvent):
            return await self._handle_anthropic_content_block_start(event, context)

        elif isinstance(event, RawContentBlockDeltaEvent):
            return await self._handle_anthropic_content_block_delta(event, context)

        elif isinstance(event, RawContentBlockStopEvent):
            return await self._handle_anthropic_content_block_stop(event, context)

        return [event]

    # ========================================================================
    # Anthropic Streaming Helpers
    # ========================================================================

    async def _handle_anthropic_content_block_start(
        self,
        event: RawContentBlockStartEvent,
        context: "PolicyContext",
    ) -> list[MessageStreamEvent]:
        """Handle content_block_start event."""
        content_block = event.content_block
        index = event.index

        # Check if this is a tool_use block
        if isinstance(content_block, ToolUseBlock):
            buffered_tool_uses = self._anthropic_buffered_tool_uses(context)
            buffered_tool_uses[index] = _BufferedAnthropicToolUse(
                id=content_block.id,
                name=content_block.name,
            )
            # Don't emit - we'll emit after judging
            return []

        return [event]

    async def _handle_anthropic_content_block_delta(
        self,
        event: RawContentBlockDeltaEvent,
        context: "PolicyContext",
    ) -> list[MessageStreamEvent]:
        """Handle content_block_delta event."""
        index = event.index
        delta = event.delta

        # Check if this is accumulating JSON for a buffered tool_use
        buffered_tool_uses = self._anthropic_buffered_tool_uses(context)
        if index in buffered_tool_uses and isinstance(delta, InputJSONDelta):
            buffered_tool_uses[index].input_json += delta.partial_json
            return []

        return [event]

    async def _handle_anthropic_content_block_stop(
        self,
        event: RawContentBlockStopEvent,
        context: "PolicyContext",
    ) -> list[MessageStreamEvent]:
        """Handle content_block_stop event - judge buffered tool_use if present."""
        index = event.index
        buffered_tool_uses = self._anthropic_buffered_tool_uses(context)

        if index not in buffered_tool_uses:
            return [cast(MessageStreamEvent, event)]

        buffered = buffered_tool_uses.pop(index)
        tool_call = self._tool_call_from_anthropic_buffer(buffered)

        blocked_result = await self._evaluate_and_maybe_block_anthropic(tool_call, context)

        if blocked_result is not None:
            self._anthropic_blocked_blocks(context).add(index)
            logger.info(f"Blocked tool call '{tool_call['name']}' in streaming")

            # Replace the tool_use block with a text block containing the blocked message
            blocked_message = self._format_anthropic_blocked_message(tool_call, blocked_result)
            text_block = TextBlock(type="text", text="")
            start_event = RawContentBlockStartEvent(type="content_block_start", index=index, content_block=text_block)
            text_delta = TextDelta(type="text_delta", text=blocked_message)
            delta_event = RawContentBlockDeltaEvent(type="content_block_delta", index=index, delta=text_delta)
            return [
                cast(MessageStreamEvent, start_event),
                cast(MessageStreamEvent, delta_event),
                cast(MessageStreamEvent, event),
            ]

        # Tool call allowed - reconstruct the full event sequence from buffered data
        logger.debug(f"Tool call '{tool_call['name']}' allowed, re-emitting buffered events")
        tool_use_block = ToolUseBlock(type="tool_use", id=buffered.id, name=buffered.name, input={})
        start_event = RawContentBlockStartEvent(type="content_block_start", index=index, content_block=tool_use_block)
        json_delta = InputJSONDelta(type="input_json_delta", partial_json=buffered.input_json or "{}")
        delta_event = RawContentBlockDeltaEvent(type="content_block_delta", index=index, delta=json_delta)
        return [
            cast(MessageStreamEvent, start_event),
            cast(MessageStreamEvent, delta_event),
            cast(MessageStreamEvent, event),
        ]

    def _extract_tool_call_from_anthropic_block(self, block: "AnthropicToolUseBlock") -> ToolCallDict:
        """Extract tool call dict from a tool_use content block dict."""
        return {
            "id": block.get("id", ""),
            "name": block.get("name", ""),
            "arguments": json.dumps(block.get("input", {})),
        }

    def _tool_call_from_anthropic_buffer(self, buffered: _BufferedAnthropicToolUse) -> ToolCallDict:
        """Create tool call dict from buffered data."""
        return {
            "id": buffered.id,
            "name": buffered.name,
            "arguments": buffered.input_json or "{}",
        }

    async def _evaluate_and_maybe_block_anthropic(
        self,
        tool_call: ToolCallDict,
        context: "PolicyContext",
    ) -> JudgeResult | None:
        """Evaluate a tool call and return JudgeResult if blocked, None if allowed."""
        name = str(tool_call.get("name", ""))
        arguments = tool_call.get("arguments", "{}")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments)

        logger.debug(f"Evaluating tool call: {name}")
        self._emit_evaluation_started(context, name, arguments, prefix="anthropic_")

        # Call judge with fail-secure error handling
        try:
            judge_result = await call_judge(
                name,
                arguments,
                self._config,
                self._judge_instructions,
                api_key=self._resolve_judge_api_key(context, self._config.api_key, self._fallback_api_key),
            )
        except Exception as exc:
            logger.error(
                f"Judge evaluation FAILED for tool call '{name}' with arguments: "
                f"{arguments[:TOOL_ARGS_TRUNCATION_LENGTH]}... Error: {exc}. DEFAULTING TO BLOCK.",
                exc_info=True,
            )
            self._emit_evaluation_failed(context, name, arguments, exc, prefix="anthropic_")
            # Return a synthetic JudgeResult for the blocked message
            return JudgeResult(
                probability=1.0,
                explanation=f"Judge evaluation failed: {exc}",
                prompt=[],
                response_text="",
            )

        logger.debug(
            f"Judge probability: {judge_result.probability:.2f} (threshold: {self._config.probability_threshold})"
        )
        self._emit_evaluation_complete(context, name, judge_result, prefix="anthropic_")

        should_block = judge_result.probability >= self._config.probability_threshold

        if should_block:
            self._emit_tool_call_blocked(context, name, judge_result, prefix="anthropic_")
            logger.warning(
                f"Blocking tool call '{name}' (probability {judge_result.probability:.2f} "
                f">= {self._config.probability_threshold})"
            )
            return judge_result
        else:
            self._emit_tool_call_allowed(context, name, judge_result.probability, prefix="anthropic_")
            return None

    def _format_anthropic_blocked_message(
        self,
        tool_call: ToolCallDict,
        judge_result: JudgeResult,
    ) -> str:
        """Format blocked message using template."""
        tool_arguments = tool_call.get("arguments", "{}")
        if not isinstance(tool_arguments, str):
            tool_arguments = json.dumps(tool_arguments)

        return self._blocked_message_template.format(
            tool_name=tool_call.get("name", "unknown"),
            tool_arguments=tool_arguments[:TOOL_ARGS_TRUNCATION_LENGTH],
            probability=judge_result.probability,
            explanation=judge_result.explanation or "No explanation provided",
        )

    # ========================================================================
    # Shared Helpers
    # ========================================================================

    def _emit_evaluation_started(
        self,
        policy_ctx: "PolicyContext",
        name: str,
        arguments: str,
        prefix: str = "",
    ) -> None:
        """Emit observability event for evaluation start."""
        event_name = f"policy.{prefix}judge.evaluation_started"
        policy_ctx.record_event(
            event_name,
            {
                "summary": f"Evaluating tool call: {name}",
                "tool_name": name,
                "tool_arguments": arguments[:TOOL_ARGS_TRUNCATION_LENGTH],
            },
        )

    def _emit_evaluation_failed(
        self,
        policy_ctx: "PolicyContext",
        name: str,
        arguments: str,
        exc: Exception,
        prefix: str = "",
    ) -> None:
        """Emit observability event for evaluation failure."""
        event_name = f"policy.{prefix}judge.evaluation_failed"
        policy_ctx.record_event(
            event_name,
            {
                "summary": f"⚠️ Judge evaluation failed for '{name}' - BLOCKED (fail-secure)",
                "tool_name": name,
                "tool_arguments": arguments[:TOOL_ARGS_TRUNCATION_LENGTH],
                "error": str(exc),
                "severity": "error",
                "action_taken": "blocked",
            },
        )

    def _emit_evaluation_complete(
        self,
        policy_ctx: "PolicyContext",
        name: str,
        judge_result: JudgeResult,
        prefix: str = "",
    ) -> None:
        """Emit observability event for successful evaluation."""
        event_name = f"policy.{prefix}judge.evaluation_complete"
        policy_ctx.record_event(
            event_name,
            {
                "summary": f"Judge evaluated '{name}': probability={judge_result.probability:.2f}",
                "tool_name": name,
                "probability": judge_result.probability,
                "threshold": self._config.probability_threshold,
                "explanation": judge_result.explanation,
            },
        )

    def _emit_tool_call_allowed(
        self,
        policy_ctx: "PolicyContext",
        name: str,
        probability: float,
        prefix: str = "",
    ) -> None:
        """Emit observability event for allowed tool call."""
        event_name = f"policy.{prefix}judge.tool_call_allowed"
        policy_ctx.record_event(
            event_name,
            {
                "summary": f"Tool call '{name}' allowed (probability {probability:.2f} < {self._config.probability_threshold})",
                "tool_name": name,
                "probability": probability,
            },
        )

    def _emit_tool_call_blocked(
        self,
        policy_ctx: "PolicyContext",
        name: str,
        judge_result: JudgeResult,
        prefix: str = "",
    ) -> None:
        """Emit observability event for blocked tool call."""
        event_name = f"policy.{prefix}judge.tool_call_blocked"
        policy_ctx.record_event(
            event_name,
            {
                "summary": f"BLOCKED: Tool call '{name}' rejected (probability {judge_result.probability:.2f} >= {self._config.probability_threshold})",
                "severity": "warning",
                "tool_name": name,
                "probability": judge_result.probability,
                "explanation": judge_result.explanation,
            },
        )


__all__ = ["ToolCallJudgePolicy", "ToolCallJudgeConfig"]
