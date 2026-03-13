"""Utilities for SimpleLLMPolicy judge calls.

Handles prompt construction, LiteLLM judge calls, and response parsing
for the simple LLM policy that applies plain-English instructions to
response blocks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, cast

from litellm import acompletion
from litellm.types.utils import Choices, Message, ModelResponse
from pydantic import BaseModel, Field

from luthien_proxy.policies.tool_call_judge_utils import parse_judge_response

logger = logging.getLogger(__name__)


class SimpleLLMJudgeConfig(BaseModel):
    """Configuration for SimpleLLMPolicy judge."""

    model: str = Field(default="claude-haiku-4-5", description="Judge model identifier")
    api_base: str | None = Field(default=None, description="API base URL")
    api_key: str | None = Field(
        default=None,
        description="API key for authentication",
        json_schema_extra={"format": "password"},
    )
    instructions: str = Field(description="Plain-English instructions for the judge")
    temperature: float = Field(default=0.0, description="Sampling temperature")
    max_tokens: int = Field(default=4096, description="Maximum output tokens")
    on_error: str = Field(
        default="block",
        pattern=r"^(pass|block)$",
        description=(
            "Action when the judge call fails. 'block' (default) is fail-secure: "
            "content is rejected when the judge cannot evaluate it. 'pass' is "
            "fail-open and INSECURE for safety-critical deployments — a judge "
            "outage silently permits all content."
        ),
    )

    model_config = {"frozen": True}


@dataclass(frozen=True)
class BlockDescriptor:
    """Describes a content block from the LLM response."""

    type: str
    content: str


@dataclass(frozen=True)
class ReplacementBlock:
    """A replacement block returned by the judge."""

    type: str
    text: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None


@dataclass(frozen=True)
class JudgeAction:
    """The judge's decision: pass the block through or replace it."""

    action: str
    blocks: tuple[ReplacementBlock, ...] | None = None


_JUDGE_SYSTEM_TEMPLATE = """\
You are a content policy judge. Your job is to evaluate LLM response blocks \
against the following instructions and decide whether to pass them through or \
replace them.

INSTRUCTIONS:
{instructions}

You must respond with a JSON object using one of these two formats:

To allow the block unchanged:
{{"action": "pass"}}

To replace the block with new content:
{{"action": "replace", "blocks": [{{"type": "text", "text": "replacement text"}}]}}

Blocks can be of type "text" (with a "text" field) or "tool_use" (with "name" \
and "input" fields).

Respond ONLY with valid JSON. No additional text."""


def build_judge_prompt(
    instructions: str,
    current_block: BlockDescriptor,
    previous_blocks: tuple[BlockDescriptor, ...],
) -> list[dict[str, str]]:
    """Build the message list for a judge LLM call."""
    system = _JUDGE_SYSTEM_TEMPLATE.format(instructions=instructions)

    user_parts: list[str] = []
    if previous_blocks:
        user_parts.append("Previous blocks in this response:")
        for i, block in enumerate(previous_blocks, 1):
            user_parts.append(f"  [{i}] ({block.type}) {block.content}")
        user_parts.append("")

    user_parts.append(f"Current block to evaluate ({current_block.type}):")
    user_parts.append(current_block.content)

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(user_parts)},
    ]


def parse_judge_action(raw: str) -> JudgeAction:
    """Parse a raw judge response string into a JudgeAction.

    Raises ValueError on malformed responses.
    """
    data = parse_judge_response(raw)

    if "action" not in data:
        raise ValueError("Judge response missing required 'action' field")

    action = data["action"]
    if action not in ("pass", "replace"):
        raise ValueError(f"Judge action must be 'pass' or 'replace', got '{action}'")

    if action == "pass":
        return JudgeAction(action="pass")

    raw_blocks = data.get("blocks")
    if not raw_blocks:
        raise ValueError("'replace' action requires non-empty 'blocks' array")

    replacement_blocks: list[ReplacementBlock] = []
    for block in raw_blocks:
        block_type = block.get("type")
        if not block_type:
            raise ValueError("Each block must have a 'type' field")

        if block_type == "text":
            if "text" not in block:
                raise ValueError("text block: 'text' field is required")
            replacement_blocks.append(ReplacementBlock(type="text", text=block["text"]))
        elif block_type == "tool_use":
            if "name" not in block:
                raise ValueError("tool_use block: 'name' field is required")
            replacement_blocks.append(
                ReplacementBlock(
                    type="tool_use",
                    name=block["name"],
                    input=block.get("input", {}),
                )
            )
        else:
            replacement_blocks.append(ReplacementBlock(type=block_type, text=block.get("text")))

    return JudgeAction(action="replace", blocks=tuple(replacement_blocks))


async def call_simple_llm_judge(
    config: SimpleLLMJudgeConfig,
    current_block: BlockDescriptor,
    previous_blocks: tuple[BlockDescriptor, ...],
) -> JudgeAction:
    """Call the judge LLM and return its decision.

    The caller (SimpleLLMPolicy) resolves API keys at init time and passes
    them via config. This function uses config values directly.
    Exceptions propagate to the caller, which applies the on_error policy.
    """
    prompt = build_judge_prompt(config.instructions, current_block, previous_blocks)

    kwargs: dict[str, Any] = {
        "model": config.model,
        "messages": prompt,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "response_format": {"type": "json_object"},
    }
    if config.api_base:
        kwargs["api_base"] = config.api_base
    if config.api_key:
        kwargs["api_key"] = config.api_key

    response = await acompletion(**kwargs)
    response = cast(ModelResponse, response)

    first_choice: Choices = cast(Choices, response.choices[0])
    message: Message = first_choice.message
    if message.content is None:
        raise ValueError("Judge response content is None")

    return parse_judge_action(message.content)


__all__ = [
    "SimpleLLMJudgeConfig",
    "BlockDescriptor",
    "ReplacementBlock",
    "JudgeAction",
    "build_judge_prompt",
    "parse_judge_action",
    "call_simple_llm_judge",
]
