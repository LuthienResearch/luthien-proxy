"""Utilities for tool call judging and Anthropic streaming tool_use buffering.

Judge-specific helpers (used by ToolCallJudgePolicy):
- Building judge prompts
- Parsing judge responses

Policy-agnostic streaming helpers (used by any policy that intercepts tool_use blocks):
- BufferedToolUse: accumulates input JSON across streaming deltas
- handle_tool_use_block_start / handle_tool_use_block_delta: buffer or pass through
- build_allowed_tool_use_events / build_blocked_text_events: reconstruct event sequences
- build_blocked_non_streaming_response: apply modified content with stop_reason fix-up
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    TextBlock,
    TextDelta,
    ToolUseBlock,
)
from pydantic import BaseModel, Field

from luthien_proxy.utils.constants import DEFAULT_JUDGE_MAX_TOKENS

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import AnthropicContentBlock, AnthropicResponse

logger = logging.getLogger(__name__)


class JudgeConfig(BaseModel):
    """Configuration for LLM judge."""

    model: str = Field(
        description="Any LiteLLM model string, e.g. 'claude-haiku-4-5', 'anthropic/claude-sonnet-4-5', 'ollama/llama3'",
    )
    api_base: str | None = Field(
        default=None,
        description="Optional. Leave blank to use the model's default backend. Set to override, e.g. for a proxy or local endpoint.",
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


@dataclass
class BufferedToolUse:
    """Streaming buffer accumulating tool_use input JSON until block_stop."""

    id: str
    name: str
    input_json: str = ""


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
    """Parse raw judge response text into a validated JudgeResult."""
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


def handle_tool_use_block_start(
    event: RawContentBlockStartEvent,
    buffer: dict[int, BufferedToolUse],
) -> list[MessageStreamEvent]:
    """Buffer ToolUseBlock start events; pass through all others."""
    if isinstance(event.content_block, ToolUseBlock):
        buffer[event.index] = BufferedToolUse(id=event.content_block.id, name=event.content_block.name)
        return []
    return [cast(MessageStreamEvent, event)]


def handle_tool_use_block_delta(
    event: RawContentBlockDeltaEvent,
    buffer: dict[int, BufferedToolUse],
) -> list[MessageStreamEvent]:
    """Accumulate InputJSONDelta for buffered tool_use blocks.

    Pass through if index is not buffered or delta is not an InputJSONDelta.
    """
    if event.index in buffer and isinstance(event.delta, InputJSONDelta):
        buffer[event.index].input_json += event.delta.partial_json
        return []
    return [cast(MessageStreamEvent, event)]


def build_allowed_tool_use_events(
    buffered: BufferedToolUse,
    stop_event: RawContentBlockStopEvent,
) -> list[MessageStreamEvent]:
    """Reconstruct the full tool_use event sequence (start + delta + stop) for an allowed tool call."""
    index = stop_event.index
    tool_use_block = ToolUseBlock(type="tool_use", id=buffered.id, name=buffered.name, input={})
    start = RawContentBlockStartEvent(type="content_block_start", index=index, content_block=tool_use_block)
    delta = RawContentBlockDeltaEvent(
        type="content_block_delta",
        index=index,
        delta=InputJSONDelta(type="input_json_delta", partial_json=buffered.input_json or "{}"),
    )
    return [
        cast(MessageStreamEvent, start),
        cast(MessageStreamEvent, delta),
        cast(MessageStreamEvent, stop_event),
    ]


def build_blocked_text_events(
    index: int,
    stop_event: RawContentBlockStopEvent,
    message: str,
) -> list[MessageStreamEvent]:
    """Build replacement text block event sequence (start + delta + stop) for a blocked tool_use."""
    text_block = TextBlock(type="text", text="")
    start = RawContentBlockStartEvent(type="content_block_start", index=index, content_block=text_block)
    delta = RawContentBlockDeltaEvent(
        type="content_block_delta",
        index=index,
        delta=TextDelta(type="text_delta", text=message),
    )
    return [
        cast(MessageStreamEvent, start),
        cast(MessageStreamEvent, delta),
        cast(MessageStreamEvent, stop_event),
    ]


def build_blocked_non_streaming_response(
    response: "AnthropicResponse",
    new_content: "list[AnthropicContentBlock]",
) -> "AnthropicResponse":
    """Build a non-streaming response with modified content, fixing stop_reason if needed.

    If new_content contains no tool_use blocks and response.stop_reason is "tool_use",
    rewrites stop_reason to "end_turn".
    """
    modified_response = dict(response)
    modified_response["content"] = new_content
    has_tool_use = any(isinstance(b, dict) and b.get("type") == "tool_use" for b in new_content)
    if not has_tool_use and modified_response.get("stop_reason") == "tool_use":
        modified_response["stop_reason"] = "end_turn"
    return cast("AnthropicResponse", modified_response)


__all__ = [
    "BufferedToolUse",
    "JudgeConfig",
    "JudgeResult",
    "build_allowed_tool_use_events",
    "build_blocked_non_streaming_response",
    "build_blocked_text_events",
    "build_judge_prompt",
    "handle_tool_use_block_delta",
    "handle_tool_use_block_start",
    "parse_judge_response",
    "parse_to_judge_result",
]
