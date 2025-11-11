# ABOUTME: Utilities for LLM-based tool call judging
# ABOUTME: Handles judge LLM calls, prompt building, and response parsing

"""Utilities for tool call judging with LLM.

This module provides the core judging functionality used by ToolCallJudgePolicy:
- Calling judge LLM to evaluate tool calls
- Building judge prompts
- Parsing judge responses
- Creating blocked response messages
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, cast

from litellm import acompletion
from litellm.types.utils import Choices, Message, ModelResponse

from luthien_proxy.v2.policy_core import create_text_response

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JudgeConfig:
    """Configuration for LLM judge.

    Attributes:
        model: LLM model identifier
        api_base: API base URL (optional)
        api_key: API key for authentication (optional)
        probability_threshold: Threshold for blocking (0-1)
        temperature: Sampling temperature for judge
        max_tokens: Maximum output tokens for judge response
    """

    model: str
    api_base: str | None
    api_key: str | None
    probability_threshold: float
    temperature: float
    max_tokens: int


@dataclass(frozen=True)
class JudgeResult:
    """Result from LLM judge evaluation."""

    probability: float
    explanation: str
    prompt: list[dict[str, str]]
    response_text: str


# TODO: This should use dependency injection for the LLM client
async def call_judge(
    name: str,
    arguments: str,
    config: JudgeConfig,
    judge_instructions: str,
) -> JudgeResult:
    """Call LLM judge to evaluate a tool call.

    Args:
        name: Tool call name
        arguments: Tool call arguments (JSON string)
        config: Judge configuration
        judge_instructions: System prompt for judge

    Returns:
        JudgeResult with probability and explanation

    Raises:
        Exception: If judge LLM call fails or response cannot be parsed
    """
    prompt = build_judge_prompt(name, arguments, judge_instructions)

    try:
        kwargs: dict[str, Any] = {
            "model": config.model,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "messages": prompt,
        }

        # Only use response_format for models that support it
        # (gpt-4-turbo, gpt-4o, gpt-3.5-turbo-1106+, etc.)
        # Skip for base gpt-4 which doesn't support it
        model_lower = config.model.lower()
        if "gpt-4o" in model_lower or "gpt-4-turbo" in model_lower or "gpt-3.5-turbo" in model_lower:
            kwargs["response_format"] = {"type": "json_object"}

        if config.api_base:
            kwargs["api_base"] = config.api_base
        if config.api_key:
            kwargs["api_key"] = config.api_key

        response = await acompletion(**kwargs)
        response = cast(ModelResponse, response)

    except Exception as exc:
        logger.error(f"LLM judge request failed: {exc}")
        raise

    # Extract response content
    first_choice: Choices = cast(Choices, response.choices[0])
    message: Message = first_choice.message
    if message.content is None:
        raise ValueError("Judge response content is None")
    content: str = message.content

    if not isinstance(content, str):
        raise ValueError("Judge response content must be a string")

    # Parse JSON response
    data = parse_judge_response(content)
    probability = float(data.get("probability", 0.0))
    explanation = str(data.get("explanation", ""))

    # Clamp probability to [0, 1]
    probability = max(0.0, min(1.0, probability))

    return JudgeResult(
        probability=probability,
        explanation=explanation,
        prompt=prompt,
        response_text=content,
    )


def parse_judge_response(content: str) -> dict[str, Any]:
    """Parse judge response JSON, handling fenced code blocks.

    Args:
        content: Raw judge response text

    Returns:
        Parsed JSON dict

    Raises:
        ValueError: If JSON parsing fails or result is not a dict
    """
    text = content.strip()

    # Handle fenced code blocks (```json ... ```)
    if text.startswith("```"):
        text = text.lstrip("`")
        newline_index = text.find("\n")
        if newline_index != -1:
            prefix = text[:newline_index].strip().lower()
            if prefix in {"json", "```json", ""}:
                text = text[newline_index + 1 :]
        text = text.rstrip("`").strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Judge response JSON parsing failed: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("Judge response must be a JSON object")

    return data


def build_judge_prompt(name: str, arguments: str, judge_instructions: str) -> list[dict[str, str]]:
    """Build prompt for judge LLM using custom instructions.

    Args:
        name: Tool call name
        arguments: Tool call arguments (JSON string)
        judge_instructions: System prompt for the judge

    Returns:
        Messages list for judge LLM
    """
    return [
        {
            "role": "system",
            "content": judge_instructions,
        },
        {
            "role": "user",
            "content": f"Tool name: {name}\nArguments: {arguments}\n\nAssess the risk.",
        },
    ]


def create_blocked_response(
    tool_call: dict[str, Any],
    judge_result: JudgeResult,
    blocked_message_template: str,
    model: str,
) -> ModelResponse:
    """Create a blocked response message using template.

    Args:
        tool_call: Tool call that was blocked
        judge_result: Judge evaluation result
        blocked_message_template: Template string with variables:
            {tool_name}, {tool_arguments}, {probability}, {explanation}
        model: Model identifier for the response

    Returns:
        ModelResponse with blocked message
    """
    # Format message using template with available variables
    tool_arguments = tool_call.get("arguments", "")
    if not isinstance(tool_arguments, str):
        tool_arguments = json.dumps(tool_arguments)

    message = blocked_message_template.format(
        tool_name=tool_call.get("name", "unknown"),
        tool_arguments=tool_arguments,
        probability=judge_result.probability,
        explanation=judge_result.explanation or "No explanation provided",
    )

    return create_text_response(message, model=model)


__all__ = [
    "JudgeConfig",
    "JudgeResult",
    "call_judge",
    "parse_judge_response",
    "build_judge_prompt",
    "create_blocked_response",
]
