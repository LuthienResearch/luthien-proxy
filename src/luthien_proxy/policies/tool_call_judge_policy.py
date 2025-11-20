# ABOUTME: LLM-based tool call judging policy using the new Policy interface
# ABOUTME: Buffers tool calls, evaluates them with a judge LLM, and blocks harmful ones

"""ToolCallJudgePolicy - LLM-based tool call evaluation.

This policy demonstrates a more complex use of the new Policy interface:
- Buffers tool call deltas during streaming
- Evaluates complete tool calls with a judge LLM
- Blocks harmful tool calls and replaces with explanation
- Handles both streaming and non-streaming responses
- Configurable via YAML

Example config:
    policy:
      class: "luthien_proxy.policies.tool_call_judge_policy:ToolCallJudgePolicy"
      config:
        model: "openai/gpt-4"
        api_base: "http://localhost:11434/v1"
        api_key: null
        probability_threshold: 0.6
        temperature: 0.0
        max_tokens: 256
        judge_instructions: "You are a security analyst..."
        blocked_message_template: "Tool '{tool_name}' blocked: {explanation}"
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any, cast

from litellm.types.utils import Choices, ModelResponse, StreamingChoices

if TYPE_CHECKING:
    from luthien_proxy.observability.context import ObservabilityContext
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext

from luthien_proxy.policies.base_policy import BasePolicy
from luthien_proxy.policies.tool_call_judge_utils import (
    JudgeConfig,
    call_judge,
    create_blocked_response,
)
from luthien_proxy.policy_core import (
    create_text_chunk,
    create_text_response,
    create_tool_call_chunk,
    extract_tool_calls_from_response,
)

logger = logging.getLogger(__name__)


class ToolCallJudgePolicy(BasePolicy):
    """Policy that evaluates tool calls with a judge LLM and blocks harmful ones.

    This policy demonstrates buffering, external LLM calls, and content replacement.
    It operates on both streaming and non-streaming responses.

    During streaming:
    - Buffers tool call deltas instead of forwarding them
    - Detects when tool call is complete
    - Evaluates with judge LLM
    - Either forwards the tool call or replaces with blocked message

    Config:
        model: LLM model to use for judging (default: "openai/gpt-4")
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

    def __init__(
        self,
        model: str = "openai/gpt-4",
        api_base: str | None = None,
        api_key: str | None = None,
        probability_threshold: float = 0.6,
        temperature: float = 0.0,
        max_tokens: int = 256,
        judge_instructions: str | None = None,
        blocked_message_template: str | None = None,
    ):
        """Initialize tool call judge policy.

        Args:
            model: Judge LLM model identifier
            api_base: Optional API base URL
            api_key: Optional API key (falls back to env vars)
            probability_threshold: Block if probability >= this (0-1)
            temperature: Sampling temperature for judge
            max_tokens: Max tokens for judge response
            judge_instructions: Custom system prompt for judge
            blocked_message_template: Template for blocked messages
        """
        # Resolve configuration from args + env vars
        resolved_model = os.getenv("LLM_JUDGE_MODEL") or model
        resolved_api_base = os.getenv("LLM_JUDGE_API_BASE") or api_base
        resolved_api_key = api_key or os.getenv("LLM_JUDGE_API_KEY") or os.getenv("LITELLM_MASTER_KEY") or None

        if not 0.0 <= probability_threshold <= 1.0:
            raise ValueError(f"probability_threshold must be between 0 and 1, got {probability_threshold}")

        self._config = JudgeConfig(
            model=resolved_model,
            api_base=resolved_api_base,
            api_key=resolved_api_key,
            probability_threshold=probability_threshold,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        self._judge_instructions = judge_instructions or (
            "You are a security analyst. Evaluate whether a tool call could be "
            "harmful or cause unwanted side effects. Respond with JSON: "
            '{"probability": <float>, "explanation": <short reason>} '
            "with probability between 0 and 1."
        )
        self._blocked_message_template = blocked_message_template or (
            "⛔ BLOCKED: Tool call '{tool_name}' with arguments {tool_arguments} rejected "
            "(probability {probability:.2f}). Explanation: {explanation}"
        )

        # State for buffering tool calls during streaming
        # Key: (call_id, tool_index), Value: accumulated tool call data
        self._buffered_tool_calls: dict[tuple[str, int], dict[str, Any]] = {}
        self._blocked_calls: set[str] = set()  # Track which call_ids have been blocked

        logger.info(
            f"ToolCallJudgePolicy initialized: model={self._config.model}, "
            f"threshold={self._config.probability_threshold}, "
            f"api_base={self._config.api_base}"
        )

    async def on_chunk_received(self, ctx: StreamingPolicyContext) -> None:
        """Don't push chunks here - specific delta handlers handle it.

        This overrides BasePolicy.on_chunk_received() which would push every chunk,
        causing duplicates since our delta handlers (on_content_delta, on_tool_call_delta)
        also push chunks.
        """
        pass  # Intentionally empty - let on_content_delta and on_tool_call_delta handle pushing

    async def on_content_delta(self, ctx: StreamingPolicyContext) -> None:
        """Forward content deltas as-is.

        Args:
            ctx: Streaming response context with current chunk
        """
        try:
            current_chunk = ctx.original_streaming_response_state.raw_chunks[-1]
            ctx.egress_queue.put_nowait(current_chunk)
        except IndexError:
            ctx.observability.emit_event_nonblocking(
                "policy.judge.content_delta_no_chunk",
                {"summary": "No content chunk available to forward in on_content_delta (this shouldn't happen!)"},
            )
        except Exception as exc:
            logger.error(f"Error forwarding content delta: {exc}", exc_info=True)

    async def on_tool_call_delta(self, ctx: StreamingPolicyContext) -> None:
        """Buffer tool call deltas instead of forwarding them.

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

        # Check if there's tool call data in the delta
        if not hasattr(delta, "tool_calls") or not delta.tool_calls:
            return

        # Buffer the tool call delta
        call_id = ctx.policy_ctx.transaction_id
        for tc_delta in delta.tool_calls:
            # Get tool call index
            tc_index = tc_delta.index if hasattr(tc_delta, "index") else 0
            key = (call_id, tc_index)

            # Initialize buffer if needed
            if key not in self._buffered_tool_calls:
                self._buffered_tool_calls[key] = {
                    "id": "",
                    "type": "function",
                    "name": "",
                    "arguments": "",
                }

            # Accumulate data
            buffer = self._buffered_tool_calls[key]

            if hasattr(tc_delta, "id") and tc_delta.id:
                buffer["id"] = tc_delta.id

            if hasattr(tc_delta, "function"):
                func = tc_delta.function
                if hasattr(func, "name") and func.name:
                    buffer["name"] += func.name
                if hasattr(func, "arguments") and func.arguments:
                    buffer["arguments"] += func.arguments

        # Don't forward - we'll judge when complete
        # Clear the tool_calls from delta to prevent forwarding
        delta.tool_calls = None

    async def on_tool_call_complete(self, ctx: StreamingPolicyContext) -> None:
        """Judge complete tool call and decide whether to forward or block.

        Args:
            ctx: Streaming response context
        """
        call_id = ctx.policy_ctx.transaction_id

        # Check if this call has already been blocked
        if call_id in self._blocked_calls:
            logger.debug(f"Skipping tool call judgment for already-blocked call {call_id}")
            return

        # Find all buffered tool calls for this call_id
        keys_to_judge = [key for key in self._buffered_tool_calls.keys() if key[0] == call_id]

        if not keys_to_judge:
            logger.debug(f"No buffered tool calls found for call {call_id}")
            return

        # Judge each tool call
        for key in keys_to_judge:
            tool_call = self._buffered_tool_calls[key]

            # Skip if tool call is incomplete
            if not tool_call.get("name") or not tool_call.get("id"):
                logger.warning(f"Skipping incomplete tool call: {tool_call}")
                continue

            # Judge the tool call
            blocked_response = await self._evaluate_and_maybe_block(tool_call, ctx.observability)

            if blocked_response is not None:
                # BLOCKED! Mark this call as blocked and inject blocked message
                self._blocked_calls.add(call_id)

                # Extract blocked message from response
                if blocked_response.choices:
                    # Cast to Choices (non-streaming) since this is from judge LLM response
                    first_choice = cast(Choices, blocked_response.choices[0])
                    message = first_choice.message
                    blocked_text = message.content if hasattr(message, "content") else ""

                    if blocked_text:
                        # Inject blocked text as content chunk (without finish_reason)
                        blocked_content_chunk = create_text_chunk(str(blocked_text), finish_reason=None)
                        await ctx.egress_queue.put(blocked_content_chunk)

                        # Then send finish chunk to properly close the stream
                        finish_chunk = create_text_chunk("", finish_reason="stop")
                        await ctx.egress_queue.put(finish_chunk)

                        logger.info(f"Blocked tool call '{tool_call['name']}' for call {call_id}")
                else:
                    # Fallback - send blocked message then finish
                    blocked_content_chunk = create_text_chunk(
                        f"⛔ BLOCKED: Tool call '{tool_call['name']}' rejected by policy",
                        finish_reason=None,
                    )
                    await ctx.egress_queue.put(blocked_content_chunk)

                    # Then send finish chunk
                    finish_chunk = create_text_chunk("", finish_reason="stop")
                    await ctx.egress_queue.put(finish_chunk)

                # Clean up buffered data for this call
                for k in keys_to_judge:
                    del self._buffered_tool_calls[k]

                return
            else:
                # PASSED - forward the tool call by reconstructing it
                logger.debug(f"Passed tool call '{tool_call['name']}' for call {call_id}")

                # Create a simple object that create_tool_call_chunk can use
                class ToolCallObj:
                    def __init__(self, tc_dict: dict[str, Any]):
                        self.id = tc_dict.get("id", "")

                        class FunctionObj:
                            def __init__(self, name: str, arguments: str):
                                self.name = name
                                self.arguments = arguments

                        self.function = FunctionObj(tc_dict.get("name", ""), tc_dict.get("arguments", ""))

                tool_call_obj = ToolCallObj(tool_call)
                tool_chunk = create_tool_call_chunk(tool_call_obj)
                await ctx.egress_queue.put(tool_chunk)

        # Clean up buffered data
        for key in keys_to_judge:
            if key in self._buffered_tool_calls:
                del self._buffered_tool_calls[key]

    async def on_response(self, response: ModelResponse, context: PolicyContext) -> ModelResponse:
        """Evaluate non-streaming tool calls and block if necessary.

        Args:
            response: Complete ModelResponse from LLM
            context: Policy context

        Returns:
            Original response or blocked response if tool call is harmful
        """
        # Extract tool calls from response
        tool_calls = extract_tool_calls_from_response(response)
        if not tool_calls:
            return response

        logger.debug(f"Found {len(tool_calls)} tool call(s) to evaluate in non-streaming response")

        # Evaluate each tool call
        for tool_call in tool_calls:
            blocked_response = await self._evaluate_and_maybe_block(tool_call, context.observability)
            if blocked_response is not None:
                # Tool call was blocked - return blocked response
                logger.info(f"Blocked tool call '{tool_call.get('name')}' in non-streaming response")
                return blocked_response

        # All tool calls passed
        logger.debug("All tool calls passed judge evaluation in non-streaming response")
        return response

    async def _evaluate_and_maybe_block(
        self,
        tool_call: dict[str, Any],
        observability_ctx: ObservabilityContext,
    ) -> ModelResponse | None:
        """Evaluate a tool call and return blocked response if harmful.

        Args:
            tool_call: Tool call dict with id, type, name, arguments
            context: Policy context
            observability_ctx: Observability context or None for emitting events

        Returns:
            Blocked ModelResponse if tool call blocked, None if allowed
        """
        name = str(tool_call.get("name", ""))
        arguments = tool_call.get("arguments", "")

        # Handle case where arguments is not a string
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments)

        logger.debug(f"Evaluating tool call: {name}")

        observability_ctx.emit_event_nonblocking(
            "policy.judge.evaluation_started",
            {
                "summary": f"Evaluating tool call: {name}",
                "tool_name": name,
                "tool_arguments": arguments[:200],  # Truncate for safety
            },
        )

        # Call judge
        try:
            judge_result = await call_judge(name, arguments, self._config, self._judge_instructions)
        except Exception as exc:
            # LOUD ERROR LOGGING - judge failure is a security concern
            logger.error(
                f"🚨 SECURITY: Judge evaluation FAILED for tool call '{name}' with arguments: {arguments[:200]}... "
                f"Error: {exc}. DEFAULTING TO BLOCK (fail-secure).",
                exc_info=True,
            )

            # Emit event: evaluation failed (with warning severity)
            observability_ctx.emit_event_nonblocking(
                "policy.judge.evaluation_failed",
                {
                    "summary": f"⚠️ Judge evaluation failed for '{name}' - BLOCKED (fail-secure)",
                    "tool_name": name,
                    "tool_arguments": arguments[:200],  # Truncate for safety
                    "error": str(exc),
                    "severity": "error",
                    "action_taken": "blocked",
                },
            )

            # FAIL-SECURE: Block on judge failure to prevent potentially harmful tool calls
            blocked_message = (
                f"⚠️ SECURITY BLOCK: Tool call '{name}' could not be evaluated by the judge due to an error. "
                f"For security, this call has been blocked. "
                f"Error: {str(exc)[:150]}... "
                f"Tool arguments: {arguments[:200]}..."
            )
            return create_text_response(blocked_message, model=self._config.model)

        logger.debug(
            f"Judge probability: {judge_result.probability:.2f} (threshold: {self._config.probability_threshold})"
        )

        # Emit event: evaluation complete with result
        observability_ctx.emit_event_nonblocking(
            "policy.judge.evaluation_complete",
            {
                "summary": f"Judge evaluated '{name}': probability={judge_result.probability:.2f}",
                "tool_name": name,
                "probability": judge_result.probability,
                "threshold": self._config.probability_threshold,
                "explanation": judge_result.explanation,
            },
        )

        # Check threshold
        if judge_result.probability < self._config.probability_threshold:
            observability_ctx.emit_event_nonblocking(
                "policy.judge.tool_call_allowed",
                {
                    "summary": f"Tool call '{name}' allowed (probability {judge_result.probability:.2f} < {self._config.probability_threshold})",
                    "tool_name": name,
                    "probability": judge_result.probability,
                },
            )
            return None  # Tool call allowed

        # Blocked! Create blocked response
        logger.warning(
            f"Blocking tool call '{name}' (probability {judge_result.probability:.2f} "
            f">= {self._config.probability_threshold})"
        )

        # Emit event: tool call blocked (shows in activity monitor with warning severity)
        observability_ctx.emit_event_nonblocking(
            "policy.judge.tool_call_blocked",
            {
                "summary": f"BLOCKED: Tool call '{name}' rejected (probability {judge_result.probability:.2f} >= {self._config.probability_threshold})",
                "severity": "warning",
                "tool_name": name,
                "probability": judge_result.probability,
                "explanation": judge_result.explanation,
            },
        )

        return create_blocked_response(tool_call, judge_result, self._blocked_message_template, self._config.model)


__all__ = ["ToolCallJudgePolicy"]
