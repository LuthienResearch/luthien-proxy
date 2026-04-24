"""Utilities for tool call judging with LLM.

This module provides the core judging pieces used by ToolCallJudgePolicy:
- Building judge prompts
- Parsing judge responses into a validated `JudgeResult`

As of PR #609 there is no LiteLLM-based helper here; the actual judge call
runs through an `InferenceProvider` that the caller resolves from its
`inference_provider:` YAML config.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from luthien_proxy.utils.constants import DEFAULT_JUDGE_MAX_TOKENS

logger = logging.getLogger(__name__)


class JudgeConfig(BaseModel):
    """Configuration for LLM judge (runtime view the policy passes to the provider)."""

    model: str = Field(
        description="Model identifier used for the judge call (e.g. 'claude-haiku-4-5').",
    )
    api_base: str | None = Field(
        default=None,
        description="Optional override for the judge backend endpoint. Used for the passthrough provider.",
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
            if prefix in {"json", ""}:
                text = text[newline_index + 1 :]
        text = text.rstrip("`").strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Judge response JSON parsing failed: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("Judge response must be a JSON object")

    return data


def parse_to_judge_result(
    response_text: str,
    prompt: list[dict[str, str]],
) -> JudgeResult:
    """Parse raw judge response text into a validated JudgeResult.

    Clamps `probability` into `[0, 1]` and requires an explanation field
    even if blank — callers log it for triage.
    """
    data = parse_judge_response(response_text)

    if "probability" not in data:
        raise ValueError("Judge response missing required 'probability' field")

    probability = float(data["probability"])
    explanation = str(data.get("explanation", ""))

    probability = max(0.0, min(1.0, probability))

    return JudgeResult(
        probability=probability,
        explanation=explanation,
        prompt=prompt,
        response_text=response_text,
    )


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
    "parse_judge_response",
    "parse_to_judge_result",
    "build_judge_prompt",
]
