"""ToolCallJudgePolicy - LLM-based tool call evaluation for Anthropic.

For each tool_use the model proposes, calls a judge LLM that returns a risk
probability. Tool calls at or above the configured threshold are replaced with
a text block containing the blocked-message template. Judge failures are
treated as block (fail-secure).

Streaming and non-streaming responses share the same `AnthropicMessageBuilder`
primitives: text passes through, tool_use blocks buffer until judged, and the
trailing-tool_use wire invariant (#708) is enforced by the builder.

Example config:
    policy:
      class: "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy"
      config:
        model: "claude-haiku-4-5"
        api_base: "http://localhost:11434/v1"
        auth_provider: "user_credentials"
        probability_threshold: 0.6
        temperature: 0.0
        max_tokens: 256
        judge_instructions: "You are a security analyst..."
        blocked_message_template: "Tool '{tool_name}' blocked: {explanation}"
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, TypedDict

from anthropic.lib.streaming import MessageStreamEvent
from pydantic import BaseModel, Field

from luthien_proxy.credentials import (
    InferenceProviderRef,
    parse_auth_provider,
    parse_inference_provider,
)
from luthien_proxy.inference.dispatch import resolve_inference_provider
from luthien_proxy.policies.tool_call_judge_utils import (
    JudgeConfig,
    JudgeResult,
    build_judge_prompt,
    parse_to_judge_result,
)
from luthien_proxy.policy_core import (
    AnthropicHookPolicy,
    BasePolicy,
    CatalogBadge,
    Category,
    UIMetadata,
)
from luthien_proxy.policy_core.anthropic_message_builder import (
    AnthropicMessageBuilder,
    BufferedTool,
    compose_tool_only_response,
)
from luthien_proxy.settings import get_settings
from luthien_proxy.utils.constants import DEFAULT_JUDGE_MAX_TOKENS, TOOL_ARGS_TRUNCATION_LENGTH

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import AnthropicResponse
    from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)


class ToolCallDict(TypedDict):
    """Extracted tool call with normalized arguments."""

    id: str
    name: str
    arguments: str


def _parse_tool_judge_provider_ref(
    inference_provider: str | dict | None,
    auth_provider: str | dict | None,
) -> InferenceProviderRef:
    """Parse inference-provider reference for the tool-call judge.

    Accepts either the new `inference_provider:` field or the deprecated
    `auth_provider:` alias (not both). `None`/`None` defaults to
    `user_credentials`.
    """
    if inference_provider is not None and auth_provider is not None:
        raise ValueError(
            "Policy config has both 'inference_provider' and 'auth_provider'; "
            "use only 'inference_provider' (the old name is deprecated)."
        )
    if inference_provider is not None:
        return parse_inference_provider(inference_provider)
    if auth_provider is not None:
        return parse_auth_provider(auth_provider)
    return parse_inference_provider(None)


class ToolCallJudgeConfig(BaseModel):
    """Configuration for ToolCallJudgePolicy."""

    model: str = Field(
        default="claude-haiku-4-5",
        description="Model identifier for the judge call (e.g. 'claude-haiku-4-5').",
    )
    api_base: str | None = Field(
        default=None,
        description="Optional override for the judge backend endpoint. Leave null to use the provider's default.",
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
    inference_provider: str | dict | None = Field(
        default=None,
        description=(
            "Judge-side inference provider reference. Options: 'user_credentials' "
            "(default), {'provider': 'name'}, {'user_then_provider': {'name': 'x', "
            "'on_fallback': 'warn'}}."
        ),
    )
    auth_provider: str | dict | None = Field(
        default=None,
        description=(
            "DEPRECATED — use 'inference_provider' instead. Accepted for "
            "backwards compatibility; logs a warning when set."
        ),
    )

    model_config = {"frozen": True}


class ToolCallJudgePolicy(BasePolicy, AnthropicHookPolicy):
    """Evaluates each tool call with a judge LLM and blocks harmful ones.

    Stateless across requests. Per-request streaming state is owned by an
    `AnthropicMessageBuilder` stored on the request context; this policy
    drives the builder via per-tool judge calls at block_stop.
    """

    # NOTE: ui_policy_preview is a UI hint. The runtime blocked message is
    # templated with the actual tool call name, arguments, probability, and
    # explanation.
    ui = UIMetadata(
        display_name="Tool Call Judge",
        short_description="Evaluates tool calls with an LLM and blocks harmful ones.",
        category=Category.ACTIVE_MONITORING,
        catalog_badges=(CatalogBadge.BLOCKS,),
        ui_policy_preview="⛔ Tool call blocked: Evaluated as harmful by the LLM safety judge.",
    )

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name for the policy."""
        return "ToolJudge"

    def __init__(self, config: ToolCallJudgeConfig | None = None):
        """Initialize with optional config. Also accepts a dict at runtime.

        Note: config=None is valid — every field has a default and the
        inference target defaults to `user_credentials` when neither
        `inference_provider` nor `auth_provider` is set.
        """
        self.config = self._init_config(config, ToolCallJudgeConfig)

        settings = get_settings()
        self._config = JudgeConfig(
            model=settings.llm_judge_model or self.config.model,
            api_base=settings.llm_judge_api_base or self.config.api_base,
            probability_threshold=self.config.probability_threshold,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )

        self._inference_provider_ref: InferenceProviderRef = _parse_tool_judge_provider_ref(
            inference_provider=self.config.inference_provider,
            auth_provider=self.config.auth_provider,
        )

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

    # ========================================================================
    # Anthropic hooks
    # ========================================================================

    async def on_anthropic_response(
        self, response: "AnthropicResponse", context: "PolicyContext"
    ) -> "AnthropicResponse":
        """Judge each tool_use block; replace blocked calls with text."""

        async def on_tool(b: AnthropicMessageBuilder, tool: BufferedTool) -> None:
            tool_call: ToolCallDict = {
                "id": tool.id,
                "name": tool.name,
                "arguments": tool.input_json or "{}",
            }
            blocked = await self._evaluate_and_maybe_block(tool_call, context)
            if blocked is not None:
                logger.info(f"Blocked tool call '{tool.name}'")
                b.commit_text(self._format_blocked_message(tool_call, blocked))
            else:
                b.buffer_tool(id=tool.id, name=tool.name, input_json=tool.input_json)

        return await compose_tool_only_response(response, on_tool)

    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        """Stream events through the per-request builder; judge each tool at block_stop."""
        builder = context.get_request_state(self, AnthropicMessageBuilder, AnthropicMessageBuilder)

        async def on_tool_stop(b: AnthropicMessageBuilder, tool: BufferedTool) -> list[MessageStreamEvent]:
            tool_call: ToolCallDict = {
                "id": tool.id,
                "name": tool.name,
                "arguments": tool.input_json or "{}",
            }
            blocked = await self._evaluate_and_maybe_block(tool_call, context)
            if blocked is not None:
                logger.info(f"Blocked tool call '{tool.name}'")
                return b.commit_text(self._format_blocked_message(tool_call, blocked))
            b.buffer_tool(id=tool.id, name=tool.name, input_json=tool.input_json)
            return []

        return await builder.dispatch_tool_only(event, on_tool_stop)

    async def on_anthropic_streaming_policy_complete(self, context: "PolicyContext") -> None:
        """Drop the per-request builder."""
        context.pop_request_state(self, AnthropicMessageBuilder)

    # ========================================================================
    # Judge call
    # ========================================================================

    async def _call_judge(
        self,
        name: str,
        arguments: str,
        context: "PolicyContext",
    ) -> JudgeResult:
        """Resolve the inference provider and run a judge call."""
        prompt = build_judge_prompt(name, arguments, self._judge_instructions)
        dispatch = await resolve_inference_provider(
            self._inference_provider_ref,
            context,
            context.inference_provider_registry,
            passthrough_default_model=self._config.model,
            passthrough_api_base=self._config.api_base,
            passthrough_name="tool_call_judge_passthrough",
        )
        result = await dispatch.provider.complete(
            prompt,
            model=self._config.model,
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
            credential_override=dispatch.credential_override,
        )
        return parse_to_judge_result(result.text, prompt)

    async def _evaluate_and_maybe_block(
        self,
        tool_call: ToolCallDict,
        context: "PolicyContext",
    ) -> JudgeResult | None:
        """Evaluate a tool call; return JudgeResult if blocked, None if allowed.

        Fail-secure: any exception from the judge is treated as block.
        """
        name = str(tool_call.get("name", ""))
        arguments = tool_call.get("arguments", "{}")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments)

        logger.debug(f"Evaluating tool call: {name}")
        self._emit_evaluation_started(context, name, arguments)

        try:
            judge_result = await self._call_judge(name, arguments, context)
        except Exception as exc:
            logger.error(
                f"Judge evaluation FAILED for tool call '{name}' with arguments: "
                f"{arguments[:TOOL_ARGS_TRUNCATION_LENGTH]}... Error: {exc}. DEFAULTING TO BLOCK.",
                exc_info=True,
            )
            self._emit_evaluation_failed(context, name, arguments, exc)
            return JudgeResult(
                probability=1.0,
                explanation=f"Judge evaluation failed: {exc}",
                prompt=[],
                response_text="",
            )

        logger.debug(
            f"Judge probability: {judge_result.probability:.2f} (threshold: {self._config.probability_threshold})"
        )
        self._emit_evaluation_complete(context, name, judge_result)

        if judge_result.probability >= self._config.probability_threshold:
            self._emit_tool_call_blocked(context, name, judge_result)
            logger.warning(
                f"Blocking tool call '{name}' (probability {judge_result.probability:.2f} "
                f">= {self._config.probability_threshold})"
            )
            return judge_result

        self._emit_tool_call_allowed(context, name, judge_result.probability)
        return None

    def _format_blocked_message(
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
    # Observability
    # ========================================================================

    def _emit_evaluation_started(self, policy_ctx: "PolicyContext", name: str, arguments: str) -> None:
        policy_ctx.record_event(
            "policy.anthropic_judge.evaluation_started",
            {
                "summary": f"Evaluating tool call: {name}",
                "tool_name": name,
                "tool_arguments": arguments[:TOOL_ARGS_TRUNCATION_LENGTH],
            },
        )

    def _emit_evaluation_failed(self, policy_ctx: "PolicyContext", name: str, arguments: str, exc: Exception) -> None:
        policy_ctx.record_event(
            "policy.anthropic_judge.evaluation_failed",
            {
                "summary": f"⚠️ Judge evaluation failed for '{name}' - BLOCKED (fail-secure)",
                "tool_name": name,
                "tool_arguments": arguments[:TOOL_ARGS_TRUNCATION_LENGTH],
                "error": str(exc),
                "severity": "error",
                "action_taken": "blocked",
            },
        )

    def _emit_evaluation_complete(self, policy_ctx: "PolicyContext", name: str, judge_result: JudgeResult) -> None:
        policy_ctx.record_event(
            "policy.anthropic_judge.evaluation_complete",
            {
                "summary": f"Judge evaluated '{name}': probability={judge_result.probability:.2f}",
                "tool_name": name,
                "probability": judge_result.probability,
                "threshold": self._config.probability_threshold,
                "explanation": judge_result.explanation,
            },
        )

    def _emit_tool_call_allowed(self, policy_ctx: "PolicyContext", name: str, probability: float) -> None:
        policy_ctx.record_event(
            "policy.anthropic_judge.tool_call_allowed",
            {
                "summary": f"Tool call '{name}' allowed (probability {probability:.2f} < {self._config.probability_threshold})",
                "tool_name": name,
                "probability": probability,
            },
        )

    def _emit_tool_call_blocked(self, policy_ctx: "PolicyContext", name: str, judge_result: JudgeResult) -> None:
        policy_ctx.record_event(
            "policy.anthropic_judge.tool_call_blocked",
            {
                "summary": f"BLOCKED: Tool call '{name}' rejected (probability {judge_result.probability:.2f} >= {self._config.probability_threshold})",
                "severity": "warning",
                "tool_name": name,
                "probability": judge_result.probability,
                "explanation": judge_result.explanation,
            },
        )


__all__ = ["ToolCallJudgePolicy", "ToolCallJudgeConfig"]
