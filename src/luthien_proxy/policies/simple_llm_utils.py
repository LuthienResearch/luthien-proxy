"""Utilities for SimpleLLMPolicy judge calls.

Handles prompt construction, LiteLLM judge calls, and response parsing
for the simple LLM policy that applies plain-English instructions to
response blocks.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from litellm import acompletion
from litellm.types.utils import Choices, Message, ModelResponse
from pydantic import BaseModel, Field

from luthien_proxy.llm.judge_client import judge_completion

if TYPE_CHECKING:
    from luthien_proxy.credentials.credential import Credential

from luthien_proxy.llm.types.anthropic import JSONObject
from luthien_proxy.policies.tool_call_judge_utils import parse_judge_response

logger = logging.getLogger(__name__)


class SimpleLLMJudgeConfig(BaseModel):
    """Configuration for SimpleLLMPolicy judge."""

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
        description="API key for authentication",
        json_schema_extra={"format": "password"},
    )
    instructions: str = Field(description="Plain-English instructions for the judge")
    temperature: float = Field(default=0.0, description="Sampling temperature")
    max_tokens: int = Field(default=4096, description="Maximum output tokens")
    on_error: str = Field(
        default="pass",
        pattern=r"^(pass|block)$",
        description=(
            "Action when the judge call fails. 'pass' (default) allows content "
            "through with an injected warning that the safety judge was unavailable. "
            "'block' is fail-secure: content is rejected when the judge cannot "
            "evaluate it."
        ),
    )
    max_retries: int = Field(
        default=2,
        ge=0,
        le=10,
        description="Max retry attempts on transient judge failures (0 = no retries).",
    )
    retry_delay: float = Field(
        default=0.5,
        ge=0.0,
        le=30.0,
        description="Seconds to wait between retries.",
    )
    auth_provider: str | dict | None = Field(
        default=None,
        description="How to obtain credentials for judge calls. "
        "Options: 'user_credentials' (default), {'server_key': 'name'}, "
        "{'user_then_server': 'name'}. Replaces api_key when set.",
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
    input: JSONObject | None = None


@dataclass(frozen=True)
class JudgeAction:
    """The judge's decision: pass the block through or replace it."""

    action: str
    blocks: tuple[ReplacementBlock, ...] | None = None
    judge_failed: bool = False


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
    api_key: str | None = None,
    extra_headers: dict[str, str] | None = None,
    credential: "Credential | None" = None,
) -> JudgeAction:
    """Call the judge LLM and return its decision.

    api_key overrides config.api_key (used for passthrough auth). extra_headers
    is used for OAuth tokens (anthropic-beta header). If neither is set, LiteLLM
    falls back to its own env-var resolution.

    credential: When provided, uses judge_completion instead of acompletion
    with raw api_key.

    Retries up to config.max_retries times with config.retry_delay between
    attempts. Exceptions propagate to the caller on final failure.
    """
    prompt = build_judge_prompt(config.instructions, current_block, previous_blocks)

    max_attempts = 1 + config.max_retries
    last_exc: Exception | None = None

    # Both credential and legacy paths are inside the retry loop so
    # transient failures (network, LLM errors) are retried regardless
    # of which auth mechanism is used.
    for attempt in range(max_attempts):
        try:
            if credential is not None:
                response_text = await judge_completion(
                    credential,
                    model=config.model,
                    messages=prompt,
                    temperature=config.temperature,
                    max_tokens=config.max_tokens,
                    api_base=config.api_base,
                    response_format={"type": "json_object"},
                )
                return parse_judge_action(response_text)

            # Legacy path: use acompletion with api_key
            resolved_key = api_key or config.api_key
            kwargs: dict[str, Any] = {
                "model": config.model,
                "messages": prompt,
                "temperature": config.temperature,
                "max_tokens": config.max_tokens,
                "response_format": {"type": "json_object"},
            }
            if config.api_base:
                kwargs["api_base"] = config.api_base
            if resolved_key:
                kwargs["api_key"] = resolved_key
            if extra_headers:
                kwargs["extra_headers"] = extra_headers

            response = await acompletion(**kwargs)
            response = cast(ModelResponse, response)

            first_choice: Choices = cast(Choices, response.choices[0])
            message: Message = first_choice.message
            if message.content is None:
                raise ValueError("Judge response content is None")

            return parse_judge_action(message.content)
        except Exception as exc:  # noqa: BLE001
            # Retry all errors, not just network/API errors. Parse failures
            # (malformed JSON, missing fields) can also succeed on a fresh
            # LLM call since the model may produce valid output next time.
            last_exc = exc
            is_last_attempt = attempt == max_attempts - 1
            if is_last_attempt:
                break
            logger.warning(
                "SimpleLLM judge attempt %d/%d failed: %r — retrying in %.1fs",
                attempt + 1,
                max_attempts,
                exc,
                config.retry_delay,
            )
            if config.retry_delay > 0:
                await asyncio.sleep(config.retry_delay)

    assert last_exc is not None  # loop always runs at least once
    raise last_exc


__all__ = [
    "SimpleLLMJudgeConfig",
    "BlockDescriptor",
    "ReplacementBlock",
    "JudgeAction",
    "build_judge_prompt",
    "parse_judge_action",
    "call_simple_llm_judge",
]
