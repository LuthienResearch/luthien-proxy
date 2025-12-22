"""Utilities for parallel rules policy LLM evaluation.

This module provides functions for evaluating rules against content using an LLM judge.
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

from litellm import acompletion
from litellm.types.utils import Choices, Message, ModelResponse

from luthien_proxy.policies.parallel_rules_config import (
    ParallelRulesJudgeConfig,
    RuleConfig,
    RuleResult,
    RuleViolation,
)

logger = logging.getLogger(__name__)

DEFAULT_JUDGE_PROMPT_TEMPLATE = """You are evaluating whether an AI response violates a specific rule.

Rule: {ruletext}

Response to evaluate:
{content}

Analyze whether this response violates the rule. Respond with a JSON object containing:
- "probability": A float between 0 and 1 indicating how likely the rule is violated (0 = definitely not violated, 1 = definitely violated)
- "explanation": A brief explanation of your assessment

Example response:
{{"probability": 0.8, "explanation": "The response contains explicit profanity in the second paragraph."}}"""


def build_rule_prompt(
    rule: RuleConfig,
    content: str,
    default_template: str = DEFAULT_JUDGE_PROMPT_TEMPLATE,
) -> list[dict[str, str]]:
    """Build the prompt for evaluating a rule against content.

    Args:
        rule: The rule configuration
        content: The content to evaluate
        default_template: Default template to use if rule doesn't specify one

    Returns:
        List of message dicts for the LLM
    """
    template = rule.judge_prompt_template or default_template
    formatted = template.format(ruletext=rule.ruletext, content=content)

    return [
        {"role": "user", "content": formatted},
    ]


def parse_rule_response(content: str) -> dict[str, Any]:
    """Parse rule evaluation response JSON.

    Args:
        content: Raw response text from judge

    Returns:
        Parsed JSON dict with probability and explanation

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
        raise ValueError(f"Rule response JSON parsing failed: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("Rule response must be a JSON object")

    return data


async def call_rule_judge(
    rule: RuleConfig,
    content: str,
    config: ParallelRulesJudgeConfig,
) -> RuleResult:
    """Call LLM judge to evaluate whether content violates a rule.

    Args:
        rule: The rule to evaluate
        content: The content to check against the rule
        config: Judge configuration

    Returns:
        RuleResult with probability and explanation

    Raises:
        Exception: If judge LLM call fails or response cannot be parsed
    """
    prompt = build_rule_prompt(rule, content)

    try:
        kwargs: dict[str, Any] = {
            "model": config.model,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "messages": prompt,
        }

        # Only use response_format for models that support it
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
        logger.error(f"Rule judge request failed for rule '{rule.name}': {exc}")
        raise

    # Extract response content
    first_choice: Choices = cast(Choices, response.choices[0])
    message: Message = first_choice.message
    if message.content is None:
        raise ValueError(f"Judge response content is None for rule '{rule.name}'")
    response_content: str = message.content

    if not isinstance(response_content, str):
        raise ValueError(f"Judge response content must be a string for rule '{rule.name}'")

    # Parse JSON response
    data = parse_rule_response(response_content)
    probability = float(data.get("probability", 0.0))
    explanation = str(data.get("explanation", ""))

    # Clamp probability to [0, 1]
    probability = max(0.0, min(1.0, probability))

    return RuleResult(
        probability=probability,
        explanation=explanation,
        prompt=prompt,
        response_text=response_content,
    )


def format_violation_message(
    violations: list[RuleViolation],
    original_content: str,
) -> str:
    """Format a violation response message from multiple violations.

    Args:
        violations: List of rule violations
        original_content: The original content that was evaluated

    Returns:
        Formatted violation message
    """
    if not violations:
        return original_content

    parts: list[str] = []

    for v in violations:
        config = v.rule.violation_response

        # Add static message if configured
        if config.static_message:
            parts.append(config.static_message)

        # Add LLM explanation if configured and available
        if config.include_llm_explanation:
            if v.is_error:
                parts.append(f"Rule '{v.rule.name}' evaluation failed (fail-secure block): {v.error}")
            elif v.result:
                formatted = config.llm_explanation_template.format(
                    rule_name=v.rule.name,
                    explanation=v.result.explanation,
                    probability=v.result.probability,
                )
                parts.append(formatted)

    violation_text = "\n".join(parts)

    # Prepend original content if any rule requests it
    include_original = any(v.rule.violation_response.include_original for v in violations)
    if include_original and original_content:
        return f"[Original response]\n{original_content}\n\n[Policy violations]\n{violation_text}"

    return violation_text


__all__ = [
    "DEFAULT_JUDGE_PROMPT_TEMPLATE",
    "build_rule_prompt",
    "parse_rule_response",
    "call_rule_judge",
    "format_violation_message",
]
