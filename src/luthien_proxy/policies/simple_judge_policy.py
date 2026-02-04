"""SimpleJudgePolicy - Easy-to-configure LLM-based safety evaluation.

This policy makes it trivial to create LLM-based judges by:
- Subclassing SimplePolicy for easy implementation
- Defining rules as a simple list of strings
- Applying to both content and tool calls
- Using a judge LLM to evaluate safety

Supports both OpenAI and Anthropic API formats via the SimplePolicy base class.

Example:
    class MyCustomPolicy(SimpleJudgePolicy):
        RULES = [
            "Never allow rm -rf commands",
            "Block requests to delete production data",
        ]
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, ClassVar, cast

from litellm.types.utils import ChatCompletionMessageToolCall

from luthien_proxy.llm.types.anthropic import AnthropicToolUseBlock
from luthien_proxy.policies.simple_policy import SimplePolicy
from luthien_proxy.policies.tool_call_judge_utils import JudgeConfig, call_judge
from luthien_proxy.policy_core import AnthropicStreamEvent
from luthien_proxy.utils.constants import DEFAULT_JUDGE_MAX_TOKENS

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicRequest,
        AnthropicResponse,
        AnthropicTextBlock,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)


class SimpleJudgePolicy(SimplePolicy):
    """Base class for simple LLM-based safety judges.

    Subclass and define RULES to create a custom judge policy.
    Supports both OpenAI (via LiteLLM) and Anthropic API formats
    by inheriting from SimplePolicy.

    Config:
        judge_model: Model to use for judging (default: "claude-3-5-sonnet-20241022")
        judge_temperature: Temperature for judge (default: 0.0)
        judge_api_base: Optional API base URL
        judge_api_key: Optional API key
        block_threshold: Probability threshold for blocking (default: 0.7)
    """

    RULES: ClassVar[list[str]] = []

    def __init__(
        self,
        judge_model: str = "claude-3-5-sonnet-20241022",
        judge_temperature: float = 0.0,
        judge_api_base: str | None = None,
        judge_api_key: str | None = None,
        block_threshold: float = 0.7,
    ):
        """Initialize simple judge policy.

        Args:
            judge_model: Model for judging
            judge_temperature: Temperature for judge
            judge_api_base: Optional API base
            judge_api_key: Optional API key
            block_threshold: Threshold for blocking (0-1)
        """
        super().__init__()
        self.judge_config = JudgeConfig(
            model=judge_model,
            api_base=judge_api_base,
            api_key=judge_api_key,
            probability_threshold=block_threshold,
            temperature=judge_temperature,
            max_tokens=DEFAULT_JUDGE_MAX_TOKENS,
        )

    def get_config(self) -> dict:
        """Return policy configuration."""
        return {
            "judge_model": self.judge_config.model,
            "judge_temperature": self.judge_config.temperature,
            "block_threshold": self.judge_config.probability_threshold,
        }

    @property
    def judge_instructions(self) -> str:
        """Build judge instructions from RULES."""
        if not self.RULES:
            return "Evaluate if this request is safe. Rules: [No specific rules defined]"

        rules_text = "\n".join(f"- {rule}" for rule in self.RULES)
        return f"""You are a safety evaluator. Determine if the request violates any of these rules:

{rules_text}

Respond with JSON:
{{"probability": <float 0-1>, "explanation": "<brief reason>"}}

probability: How confident you are this violates the rules (0=safe, 1=definitely violates)
explanation: Brief explanation of your decision"""

    # ========== Override SimplePolicy hooks ==========

    async def simple_on_request(self, request_str: str, context: "PolicyContext") -> str:
        """Evaluate request content with judge."""
        if not self.RULES:
            return request_str

        result = await call_judge(
            name="user_request",
            arguments=json.dumps({"content": request_str}),
            config=self.judge_config,
            judge_instructions=self.judge_instructions,
        )

        context.record_event(
            "policy.simple_judge.request_evaluated",
            {
                "probability": result.probability,
                "explanation": result.explanation,
                "blocked": result.probability >= self.judge_config.probability_threshold,
            },
        )

        if result.probability >= self.judge_config.probability_threshold:
            raise ValueError(
                f"Request blocked by {self.__class__.__name__}: {result.explanation} "
                f"(confidence: {result.probability:.2f})"
            )

        return request_str

    async def simple_on_response_content(self, content: str, context: "PolicyContext") -> str:
        """Evaluate response content with judge."""
        if not self.RULES:
            return content

        result = await call_judge(
            name="assistant_response",
            arguments=json.dumps({"content": content}),
            config=self.judge_config,
            judge_instructions=self.judge_instructions,
        )

        context.record_event(
            "policy.simple_judge.response_evaluated",
            {
                "probability": result.probability,
                "explanation": result.explanation,
                "blocked": result.probability >= self.judge_config.probability_threshold,
            },
        )

        if result.probability >= self.judge_config.probability_threshold:
            return f"[Content blocked by {self.__class__.__name__}: {result.explanation}]"

        return content

    async def simple_on_response_tool_call(
        self, tool_call: ChatCompletionMessageToolCall, context: "PolicyContext"
    ) -> ChatCompletionMessageToolCall:
        """Evaluate tool call with judge."""
        if not self.RULES:
            return tool_call

        result = await call_judge(
            name=tool_call.function.name or "unknown_tool",
            arguments=tool_call.function.arguments or "{}",
            config=self.judge_config,
            judge_instructions=self.judge_instructions,
        )

        context.record_event(
            "policy.simple_judge.tool_call_evaluated",
            {
                "tool_name": tool_call.function.name,
                "probability": result.probability,
                "explanation": result.explanation,
                "blocked": result.probability >= self.judge_config.probability_threshold,
            },
        )

        if result.probability >= self.judge_config.probability_threshold:
            # Block by replacing arguments with error message
            tool_call.function.arguments = json.dumps(
                {
                    "error": f"Tool call blocked by {self.__class__.__name__}",
                    "reason": result.explanation,
                    "confidence": result.probability,
                }
            )

        return tool_call

    async def simple_on_anthropic_tool_call(
        self, tool_block: "AnthropicToolUseBlock", context: "PolicyContext"
    ) -> "AnthropicToolUseBlock":
        """Evaluate Anthropic tool use with judge."""
        if not self.RULES:
            return tool_block

        tool_name = tool_block.get("name", "unknown_tool")
        tool_input = tool_block.get("input", {})

        result = await call_judge(
            name=tool_name,
            arguments=json.dumps(tool_input),
            config=self.judge_config,
            judge_instructions=self.judge_instructions,
        )

        context.record_event(
            "policy.simple_judge.anthropic_tool_use_evaluated",
            {
                "tool_name": tool_name,
                "probability": result.probability,
                "explanation": result.explanation,
                "blocked": result.probability >= self.judge_config.probability_threshold,
            },
        )

        if result.probability >= self.judge_config.probability_threshold:
            # Replace input with error information
            tool_block["input"] = {
                "error": f"Tool call blocked by {self.__class__.__name__}",
                "reason": result.explanation,
                "confidence": result.probability,
            }

        return tool_block

    # ========== Anthropic-specific overrides ==========

    def _extract_request_content(self, request: "AnthropicRequest") -> str:
        """Extract text content from Anthropic request for evaluation."""
        messages = request.get("messages", [])
        content_parts = []

        for msg in messages:
            msg_content = msg.get("content")
            if isinstance(msg_content, str):
                content_parts.append(f"{msg.get('role', 'unknown')}: {msg_content}")
            elif isinstance(msg_content, list):
                for block in msg_content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        content_parts.append(f"{msg.get('role', 'unknown')}: {text}")

        system = request.get("system")
        if isinstance(system, str):
            content_parts.insert(0, f"system: {system}")
        elif isinstance(system, list):
            for sys_block in system:
                if isinstance(sys_block, dict) and sys_block.get("type") == "text":
                    content_parts.insert(0, f"system: {sys_block.get('text', '')}")

        return "\n".join(content_parts)

    def _extract_response_content(self, response: "AnthropicResponse") -> str:
        """Extract text content from Anthropic response for evaluation."""
        content_parts = []
        for block in response.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    content_parts.append(text)
        return "\n".join(content_parts)

    async def on_anthropic_request(self, request: "AnthropicRequest", context: "PolicyContext") -> "AnthropicRequest":
        """Evaluate Anthropic request content with judge.

        Overrides SimplePolicy to apply judge evaluation with full request context.
        Unlike the OpenAI path (which uses simple_on_request with just the last message),
        this extracts system prompt + all messages for evaluation.

        Note: We don't call super() here to avoid double evaluation (super would
        call simple_on_request which also runs the judge).
        """
        if not self.RULES:
            return request

        request_content = self._extract_request_content(request)
        if not request_content:
            return request

        result = await call_judge(
            name="user_request",
            arguments=json.dumps({"content": request_content}),
            config=self.judge_config,
            judge_instructions=self.judge_instructions,
        )

        context.record_event(
            "policy.simple_judge.anthropic_request_evaluated",
            {
                "probability": result.probability,
                "explanation": result.explanation,
                "blocked": result.probability >= self.judge_config.probability_threshold,
            },
        )

        if result.probability >= self.judge_config.probability_threshold:
            raise ValueError(
                f"Request blocked by {self.__class__.__name__}: {result.explanation} "
                f"(confidence: {result.probability:.2f})"
            )

        return request

    async def on_anthropic_response(
        self, response: "AnthropicResponse", context: "PolicyContext"
    ) -> "AnthropicResponse":
        """Evaluate Anthropic response content with judge.

        Overrides SimplePolicy completely to handle evaluation directly.
        Text content is evaluated via the judge; tool use blocks are evaluated
        via simple_on_anthropic_tool_call.

        Note: We don't call super() to avoid double evaluation.
        """
        if not self.RULES:
            return response

        # Evaluate text content
        response_content = self._extract_response_content(response)
        if response_content:
            result = await call_judge(
                name="assistant_response",
                arguments=json.dumps({"content": response_content}),
                config=self.judge_config,
                judge_instructions=self.judge_instructions,
            )

            context.record_event(
                "policy.simple_judge.anthropic_response_evaluated",
                {
                    "probability": result.probability,
                    "explanation": result.explanation,
                    "blocked": result.probability >= self.judge_config.probability_threshold,
                },
            )

            if result.probability >= self.judge_config.probability_threshold:
                # Replace text content with blocked message
                for block in response.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_block = cast("AnthropicTextBlock", block)
                        text_block["text"] = f"[Content blocked by {self.__class__.__name__}: {result.explanation}]"

        # Evaluate tool use blocks
        content_blocks = response.get("content", [])
        for i, block in enumerate(content_blocks):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tool_block: AnthropicToolUseBlock = {
                    "type": "tool_use",
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "input": block.get("input", {}),
                }
                transformed = await self.simple_on_anthropic_tool_call(tool_block, context)
                content_blocks[i] = transformed

        return response

    async def on_anthropic_stream_event(
        self, event: AnthropicStreamEvent, context: "PolicyContext"
    ) -> AnthropicStreamEvent | None:
        """Pass through stream events with SimplePolicy buffering.

        Note: Stream-level blocking is not implemented for the simple judge.
        For streaming responses, evaluation happens when blocks complete.
        """
        return await super().on_anthropic_stream_event(event, context)


__all__ = ["SimpleJudgePolicy"]
