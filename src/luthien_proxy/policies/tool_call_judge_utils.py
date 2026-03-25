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
from typing import Any

from pydantic import BaseModel, Field

from luthien_proxy.llm.completion import completion

from luthien_proxy.utils.constants import DEFAULT_JUDGE_MAX_TOKENS

logger = logging.getLogger(__name__)


class JudgeConfig(BaseModel):
    """Configuration for LLM judge."""

    model: str = Field(
        description="Anthropic model name, e.g. 'claude-haiku-4-5', 'claude-sonnet-4-5-20250514'",
    )
    base_url: str | None = Field(
        default=None,
        description="Optional custom API base URL.",
    )
    api_key: str | None = Field(
        default=None,
        description="API key for authentication",
        json_schema_extra={"format": "password"},
    )
    probability_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Threshold for blocking tool calls (0-1)",
    )
    temperature: float = Field(default=0.0, description="Sampling temperature for judge")
    max_tokens: int = Field(
        default=DEFAULT_JUDGE_MAX_TOKENS,
        description="Maximum output tokens for judge response",
    )

    model_config = {"frozen": True}


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
    api_key: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> JudgeResult:
    """Call LLM judge to evaluate a tool call.

    api_key overrides config.api_key (used for passthrough auth). extra_headers
    is used for OAuth tokens (anthropic-beta header). If neither is set, LiteLLM
    falls back to its own env-var resolution.

    Args:
        name: Tool call name
        arguments: Tool call arguments (JSON string)
        config: Judge configuration
        judge_instructions: System prompt for judge
        api_key: API key override (e.g. from request passthrough)
        extra_headers: Extra HTTP headers (e.g. OAuth beta header)

    Returns:
        JudgeResult with probability and explanation

    Raises:
        Exception: If judge LLM call fails or response cannot be parsed
    """
    prompt = build_judge_prompt(name, arguments, judge_instructions)
    resolved_key = api_key or config.api_key

    try:
        kwargs: dict[str, Any] = {
            "model": config.model,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "messages": prompt,
        }

        if config.base_url:
            kwargs["base_url"] = config.base_url
        if resolved_key:
            kwargs["api_key"] = resolved_key
        if extra_headers:
            kwargs["extra_headers"] = extra_headers

        result = await completion(**kwargs)

    except Exception as exc:
        logger.error(f"LLM judge request failed: {exc}")
        raise

    content = result.text

    # Parse JSON response
    data = parse_judge_response(content)

    # Fail-secure: missing probability field is a malformed response
    if "probability" not in data:
        raise ValueError("Judge response missing required 'probability' field")

    probability = float(data["probability"])
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


__all__ = [
    "JudgeConfig",
    "JudgeResult",
    "call_judge",
    "parse_judge_response",
    "build_judge_prompt",
]
