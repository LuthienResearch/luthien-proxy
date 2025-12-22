"""ParallelRulesPolicy - Evaluate responses against multiple rules in parallel.

This policy checks LLM responses against a configurable set of rules, where each
rule is evaluated by an LLM judge in parallel. Rules can apply to text responses,
tool calls, or both, with per-rule violation handling configuration.

Example config:
    policy:
      class: "luthien_proxy.policies.parallel_rules_policy:ParallelRulesPolicy"
      config:
        judge:
          model: "anthropic/claude-3-haiku-20240307"
          probability_threshold: 0.5
        rules:
          - name: "no_profanity"
            ruletext: "Does this response contain profanity?"
            response_types: ["text"]
            violation_response:
              static_message: "Response blocked due to profanity."
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, cast

from litellm.types.utils import (
    ChatCompletionMessageToolCall,
    Choices,
    Function,
    ModelResponse,
    StreamingChoices,
)

from luthien_proxy.policies.base_policy import BasePolicy
from luthien_proxy.policies.parallel_rules_config import (
    ParallelRulesJudgeConfig,
    ResponseType,
    RuleConfig,
    RuleResult,
    RuleViolation,
)
from luthien_proxy.policies.parallel_rules_utils import (
    call_rule_judge,
    format_violation_message,
)
from luthien_proxy.policy_core import (
    create_finish_chunk,
    create_text_chunk,
    create_text_response,
    create_tool_call_chunk,
    extract_tool_calls_from_response,
)
from luthien_proxy.settings import get_settings
from luthien_proxy.streaming.stream_blocks import ContentStreamBlock, ToolCallStreamBlock

if TYPE_CHECKING:
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext

logger = logging.getLogger(__name__)


class ParallelRulesPolicy(BasePolicy):
    """Policy that evaluates responses against multiple rules in parallel.

    Each rule specifies:
    - ruletext: The rule description passed to the LLM judge
    - response_types: Which response types the rule applies to (text, tool_call, other)
    - violation_response: How to format the response if the rule is violated

    All applicable rules are evaluated in parallel using asyncio.gather().
    If any rules are violated, the response is replaced with a violation message
    that aggregates all violations.
    """

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name for the policy."""
        return "ParallelRules"

    def __init__(
        self,
        judge: dict[str, Any] | None = None,
        rules: list[dict[str, Any]] | None = None,
    ):
        """Initialize parallel rules policy.

        Args:
            judge: Judge LLM configuration dict with keys:
                - model: LLM model identifier (required)
                - api_base: Optional API base URL
                - api_key: Optional API key
                - temperature: Sampling temperature (default: 0.0)
                - max_tokens: Max tokens for judge (default: 256)
                - probability_threshold: Default violation threshold (default: 0.5)
            rules: List of rule configuration dicts, each with keys:
                - name: Rule identifier (required)
                - ruletext: Rule description for judge (required)
                - response_types: List of response types (default: ["text"])
                - probability_threshold: Override threshold for this rule
                - judge_prompt_template: Custom prompt template
                - violation_response: Violation response configuration

        Raises:
            ValueError: If configuration is invalid
        """
        if judge is None:
            raise ValueError("ParallelRulesPolicy requires 'judge' configuration")
        if rules is None or len(rules) == 0:
            raise ValueError("ParallelRulesPolicy requires at least one rule")

        # Resolve judge configuration with env var fallbacks
        settings = get_settings()
        judge_config_dict = dict(judge)

        # Apply env var fallbacks for model/api_base/api_key
        if not judge_config_dict.get("model"):
            if settings.llm_judge_model:
                judge_config_dict["model"] = settings.llm_judge_model
            else:
                raise ValueError("Judge configuration must specify 'model'")

        if not judge_config_dict.get("api_base") and settings.llm_judge_api_base:
            judge_config_dict["api_base"] = settings.llm_judge_api_base

        if not judge_config_dict.get("api_key"):
            judge_config_dict["api_key"] = settings.llm_judge_api_key or settings.litellm_master_key or None

        self._judge_config = ParallelRulesJudgeConfig.from_dict(judge_config_dict)

        # Parse rule configurations
        self._rules: list[RuleConfig] = []
        for rule_dict in rules:
            self._rules.append(RuleConfig.from_dict(rule_dict))

        # Per-request state is stored in PolicyContext.scratchpad, not instance variables,
        # to avoid concurrency issues when the policy is shared across requests.
        # Keys used in scratchpad:
        #   - "parallel_rules_buffered_tool_calls": dict[int, dict[str, Any]]
        #   - "parallel_rules_blocked": bool

        logger.info(
            f"ParallelRulesPolicy initialized: "
            f"model={self._judge_config.model}, "
            f"rules={[r.name for r in self._rules]}, "
            f"default_threshold={self._judge_config.probability_threshold}"
        )

    def _get_buffered_tool_calls(self, ctx: PolicyContext) -> dict[int, dict[str, Any]]:
        """Get or initialize the buffered tool calls from scratchpad."""
        key = "parallel_rules_buffered_tool_calls"
        if key not in ctx.scratchpad:
            ctx.scratchpad[key] = {}
        return ctx.scratchpad[key]

    def _is_blocked(self, ctx: PolicyContext) -> bool:
        """Check if this request has been blocked."""
        return ctx.scratchpad.get("parallel_rules_blocked", False)

    def _set_blocked(self, ctx: PolicyContext) -> None:
        """Mark this request as blocked."""
        ctx.scratchpad["parallel_rules_blocked"] = True

    async def on_chunk_received(self, ctx: StreamingPolicyContext) -> None:
        """Don't push chunks here - specific delta handlers handle it.

        This overrides BasePolicy.on_chunk_received() to prevent duplicate chunks.
        """
        pass

    async def on_content_delta(self, ctx: StreamingPolicyContext) -> None:
        """Buffer content deltas - we'll evaluate and send on completion.

        We don't forward content immediately because we need to wait for the
        complete content to evaluate rules.
        """
        # Don't forward - we'll emit after evaluation in on_content_complete
        pass

    async def on_tool_call_delta(self, ctx: StreamingPolicyContext) -> None:
        """Buffer tool call deltas for later evaluation.

        Args:
            ctx: Streaming response context with current chunk
        """
        if not ctx.original_streaming_response_state.raw_chunks:
            return
        current_chunk = ctx.original_streaming_response_state.raw_chunks[-1]
        if not current_chunk.choices:
            return

        choice = current_chunk.choices[0]
        choice = cast(StreamingChoices, choice)
        delta = choice.delta

        if not hasattr(delta, "tool_calls") or not delta.tool_calls:
            return

        buffered_tool_calls = self._get_buffered_tool_calls(ctx.policy_ctx)
        for tc_delta in delta.tool_calls:
            tc_index = tc_delta.index if hasattr(tc_delta, "index") else 0

            if tc_index not in buffered_tool_calls:
                buffered_tool_calls[tc_index] = {
                    "id": "",
                    "type": "function",
                    "name": "",
                    "arguments": "",
                }

            buffer = buffered_tool_calls[tc_index]

            if hasattr(tc_delta, "id") and tc_delta.id:
                buffer["id"] = tc_delta.id

            if hasattr(tc_delta, "function"):
                func = tc_delta.function
                # Tool names are sent in a single delta, use assignment not concatenation
                if hasattr(func, "name") and func.name:
                    buffer["name"] = func.name
                # Arguments are streamed character by character, use concatenation
                if hasattr(func, "arguments") and func.arguments:
                    buffer["arguments"] += func.arguments

        # Clear tool_calls from delta to prevent accidental forwarding
        delta.tool_calls = None

    async def on_content_complete(self, ctx: StreamingPolicyContext) -> None:
        """Evaluate text rules when content is complete.

        Args:
            ctx: Streaming response context
        """
        just_completed = ctx.original_streaming_response_state.just_completed
        if not isinstance(just_completed, ContentStreamBlock):
            return

        content = just_completed.content
        if not content:
            return

        if self._is_blocked(ctx.policy_ctx):
            return

        # Evaluate all text rules in parallel
        violations = await self._evaluate_rules_parallel(
            content=content,
            content_type=ResponseType.TEXT,
            ctx=ctx,
        )

        if violations:
            self._set_blocked(ctx.policy_ctx)
            await self._send_violation_response_streaming(ctx, violations, content)
        else:
            # No violations - send the original content
            await self._send_original_content_streaming(ctx, content)

    async def on_tool_call_complete(self, ctx: StreamingPolicyContext) -> None:
        """Evaluate tool_call rules when tool call is complete.

        Args:
            ctx: Streaming response context
        """
        if self._is_blocked(ctx.policy_ctx):
            return

        just_completed = ctx.original_streaming_response_state.just_completed
        if not isinstance(just_completed, ToolCallStreamBlock):
            return

        tc_index = just_completed.index
        buffered_tool_calls = self._get_buffered_tool_calls(ctx.policy_ctx)

        if tc_index not in buffered_tool_calls:
            logger.warning(f"No buffered data for tool call index {tc_index}")
            return

        tool_call = buffered_tool_calls[tc_index]
        if not tool_call.get("name") or not tool_call.get("id"):
            logger.warning(f"Incomplete tool call data: {tool_call}")
            return

        # Format tool call as content for rule evaluation
        tool_call_content = self._format_tool_call_for_evaluation(tool_call)

        # Evaluate tool_call rules in parallel
        violations = await self._evaluate_rules_parallel(
            content=tool_call_content,
            content_type=ResponseType.TOOL_CALL,
            ctx=ctx,
        )

        if violations:
            self._set_blocked(ctx.policy_ctx)
            await self._send_violation_response_streaming(ctx, violations, tool_call_content)
        else:
            # No violations - forward the tool call
            await self._send_original_tool_call_streaming(ctx, tool_call)

    async def on_stream_complete(self, ctx: StreamingPolicyContext) -> None:
        """Emit finish chunk if needed.

        Args:
            ctx: Streaming response context
        """
        finish_reason = ctx.original_streaming_response_state.finish_reason
        if not finish_reason:
            return

        if self._is_blocked(ctx.policy_ctx):
            # Already sent finish chunk with violation response
            return

        blocks = ctx.original_streaming_response_state.blocks
        has_tool_calls = any(isinstance(b, ToolCallStreamBlock) for b in blocks)

        if has_tool_calls:
            raw_chunks = ctx.original_streaming_response_state.raw_chunks
            last_chunk = raw_chunks[-1] if raw_chunks else None
            chunk_id = last_chunk.id if last_chunk else None
            model = last_chunk.model if last_chunk else "luthien-policy"

            finish_chunk = create_finish_chunk(
                finish_reason=finish_reason,
                model=model,
                chunk_id=chunk_id,
            )
            await ctx.egress_queue.put(finish_chunk)

    async def on_response(self, response: ModelResponse, context: PolicyContext) -> ModelResponse:
        """Evaluate non-streaming response against all applicable rules.

        Args:
            response: Complete ModelResponse from LLM
            context: Policy context

        Returns:
            Original response or violation response if any rules violated
        """
        # Extract content
        content = self._extract_content_from_response(response)

        # Check text rules against content
        if content:
            text_violations = await self._evaluate_rules_parallel_nonstreaming(
                content=content,
                content_type=ResponseType.TEXT,
                context=context,
            )
            if text_violations:
                violation_message = format_violation_message(text_violations, content)
                return create_text_response(violation_message, model=self._judge_config.model)

        # Check tool_call rules against tool calls
        tool_calls = extract_tool_calls_from_response(response)
        for tool_call in tool_calls:
            tool_call_content = self._format_tool_call_for_evaluation(tool_call)
            tool_violations = await self._evaluate_rules_parallel_nonstreaming(
                content=tool_call_content,
                content_type=ResponseType.TOOL_CALL,
                context=context,
            )
            if tool_violations:
                violation_message = format_violation_message(tool_violations, tool_call_content)
                return create_text_response(violation_message, model=self._judge_config.model)

        return response

    async def on_streaming_policy_complete(self, ctx: StreamingPolicyContext) -> None:
        """Clean up per-request state.

        Args:
            ctx: Streaming response context

        Note: Per-request state is stored in PolicyContext.scratchpad, which is
        automatically cleaned up when the request completes. No explicit cleanup needed.
        """
        pass

    async def _evaluate_rules_parallel(
        self,
        content: str,
        content_type: ResponseType,
        ctx: StreamingPolicyContext,
    ) -> list[RuleViolation]:
        """Evaluate applicable rules in parallel during streaming.

        Args:
            content: Content to evaluate
            content_type: Type of content (text, tool_call)
            ctx: Streaming policy context

        Returns:
            List of rule violations (empty if no violations)
        """
        # Filter rules by response type
        applicable_rules = [r for r in self._rules if content_type in r.response_types]
        if not applicable_rules:
            return []

        # Keep connection alive during parallel LLM calls
        ctx.keepalive()

        # Run all rules in parallel
        results = await asyncio.gather(
            *[self._evaluate_single_rule(rule, content, ctx.policy_ctx) for rule in applicable_rules],
            return_exceptions=True,
        )

        ctx.keepalive()

        # Collect violations
        violations: list[RuleViolation] = []
        for rule, result in zip(applicable_rules, results):
            if isinstance(result, BaseException):
                # Fail-secure: treat errors as violations
                error = result if isinstance(result, Exception) else Exception(str(result))
                logger.error(
                    f"Rule '{rule.name}' evaluation failed (fail-secure): {result}",
                    exc_info=True,
                )
                violations.append(RuleViolation(rule=rule, result=None, error=error))
                self._emit_rule_error(ctx.policy_ctx, rule, error)
            else:
                rule_result = cast(RuleResult, result)
                if rule_result.probability >= rule.get_threshold(self._judge_config.probability_threshold):
                    violations.append(RuleViolation(rule=rule, result=rule_result))
                    self._emit_rule_violated(ctx.policy_ctx, rule, rule_result)
                else:
                    self._emit_rule_passed(ctx.policy_ctx, rule, rule_result)

        return violations

    async def _evaluate_rules_parallel_nonstreaming(
        self,
        content: str,
        content_type: ResponseType,
        context: PolicyContext,
    ) -> list[RuleViolation]:
        """Evaluate applicable rules in parallel for non-streaming responses.

        Args:
            content: Content to evaluate
            content_type: Type of content (text, tool_call)
            context: Policy context

        Returns:
            List of rule violations (empty if no violations)
        """
        applicable_rules = [r for r in self._rules if content_type in r.response_types]
        if not applicable_rules:
            return []

        results = await asyncio.gather(
            *[self._evaluate_single_rule(rule, content, context) for rule in applicable_rules],
            return_exceptions=True,
        )

        violations: list[RuleViolation] = []
        for rule, result in zip(applicable_rules, results):
            if isinstance(result, BaseException):
                error = result if isinstance(result, Exception) else Exception(str(result))
                logger.error(
                    f"Rule '{rule.name}' evaluation failed (fail-secure): {result}",
                    exc_info=True,
                )
                violations.append(RuleViolation(rule=rule, result=None, error=error))
                self._emit_rule_error(context, rule, error)
            else:
                rule_result = cast(RuleResult, result)
                if rule_result.probability >= rule.get_threshold(self._judge_config.probability_threshold):
                    violations.append(RuleViolation(rule=rule, result=rule_result))
                    self._emit_rule_violated(context, rule, rule_result)
                else:
                    self._emit_rule_passed(context, rule, rule_result)

        return violations

    async def _evaluate_single_rule(
        self,
        rule: RuleConfig,
        content: str,
        context: PolicyContext,
    ) -> Any:
        """Evaluate a single rule against content.

        Args:
            rule: Rule to evaluate
            content: Content to check
            context: Policy context

        Returns:
            RuleResult from judge
        """
        self._emit_rule_evaluation_started(context, rule, content)
        return await call_rule_judge(rule, content, self._judge_config)

    def _format_tool_call_for_evaluation(self, tool_call: dict[str, Any]) -> str:
        """Format a tool call as text content for rule evaluation.

        Args:
            tool_call: Tool call dict with name, arguments

        Returns:
            Formatted string representation of the tool call
        """
        name = tool_call.get("name", "unknown")
        arguments = tool_call.get("arguments", "")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments)

        return f"Tool call: {name}\nArguments: {arguments}"

    def _extract_content_from_response(self, response: ModelResponse) -> str:
        """Extract text content from a non-streaming response.

        Args:
            response: ModelResponse from LLM

        Returns:
            Text content string (may be empty)
        """
        if not hasattr(response, "choices") or not response.choices:
            return ""

        first_choice = cast(Choices, response.choices[0])
        if not hasattr(first_choice, "message"):
            return ""

        message = first_choice.message
        if hasattr(message, "content") and message.content:
            return str(message.content)

        return ""

    async def _send_violation_response_streaming(
        self,
        ctx: StreamingPolicyContext,
        violations: list[RuleViolation],
        original_content: str,
    ) -> None:
        """Send violation response during streaming.

        Args:
            ctx: Streaming policy context
            violations: List of violations
            original_content: Original content that was evaluated
        """
        violation_message = format_violation_message(violations, original_content)

        # Send violation text
        text_chunk = create_text_chunk(violation_message, finish_reason=None)
        await ctx.egress_queue.put(text_chunk)

        # Send finish chunk
        finish_chunk = create_text_chunk("", finish_reason="stop")
        await ctx.egress_queue.put(finish_chunk)

        logger.info(f"Blocked response due to {len(violations)} rule violation(s): {[v.rule.name for v in violations]}")

    async def _send_original_content_streaming(
        self,
        ctx: StreamingPolicyContext,
        content: str,
    ) -> None:
        """Send original content after it passed rule evaluation.

        Args:
            ctx: Streaming policy context
            content: Original content to send
        """
        # Get finish reason from original stream
        finish_reason = ctx.original_streaming_response_state.finish_reason

        # Send content chunk
        text_chunk = create_text_chunk(content, finish_reason=finish_reason)
        await ctx.egress_queue.put(text_chunk)

    async def _send_original_tool_call_streaming(
        self,
        ctx: StreamingPolicyContext,
        tool_call: dict[str, Any],
    ) -> None:
        """Send original tool call after it passed rule evaluation.

        Args:
            ctx: Streaming policy context
            tool_call: Tool call data to send
        """
        tool_call_obj = ChatCompletionMessageToolCall(
            id=tool_call.get("id", ""),
            function=Function(
                name=tool_call.get("name", ""),
                arguments=tool_call.get("arguments", ""),
            ),
        )
        chunk = create_tool_call_chunk(tool_call_obj)
        await ctx.egress_queue.put(chunk)

    def _emit_rule_evaluation_started(
        self,
        context: PolicyContext,
        rule: RuleConfig,
        content: str,
    ) -> None:
        """Emit observability event for rule evaluation start."""
        context.record_event(
            "policy.parallel_rules.evaluation_started",
            {
                "summary": f"Evaluating rule '{rule.name}'",
                "rule_name": rule.name,
                "content_preview": content[:200] if len(content) > 200 else content,
            },
        )

    def _emit_rule_passed(
        self,
        context: PolicyContext,
        rule: RuleConfig,
        result: Any,
    ) -> None:
        """Emit observability event for rule that passed."""
        context.record_event(
            "policy.parallel_rules.rule_passed",
            {
                "summary": f"Rule '{rule.name}' passed (prob={result.probability:.2f})",
                "rule_name": rule.name,
                "probability": result.probability,
                "threshold": rule.get_threshold(self._judge_config.probability_threshold),
                "explanation": result.explanation,
            },
        )

    def _emit_rule_violated(
        self,
        context: PolicyContext,
        rule: RuleConfig,
        result: Any,
    ) -> None:
        """Emit observability event for rule violation."""
        context.record_event(
            "policy.parallel_rules.rule_violated",
            {
                "summary": f"Rule '{rule.name}' violated (prob={result.probability:.2f})",
                "severity": "warning",
                "rule_name": rule.name,
                "probability": result.probability,
                "threshold": rule.get_threshold(self._judge_config.probability_threshold),
                "explanation": result.explanation,
            },
        )

    def _emit_rule_error(
        self,
        context: PolicyContext,
        rule: RuleConfig,
        error: Exception,
    ) -> None:
        """Emit observability event for rule evaluation error."""
        context.record_event(
            "policy.parallel_rules.rule_error",
            {
                "summary": f"Rule '{rule.name}' evaluation failed (fail-secure block)",
                "severity": "error",
                "rule_name": rule.name,
                "error": str(error),
                "action_taken": "blocked",
            },
        )


__all__ = ["ParallelRulesPolicy"]
