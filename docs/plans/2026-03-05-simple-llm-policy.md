# SimpleLLMPolicy Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a policy that applies plain-English instructions to LLM response blocks using a configurable judge LLM, with pass-through and flexible replacement support.

**Architecture:** BasePolicy + OpenAIPolicyInterface + AnthropicExecutionInterface, following ToolCallJudgePolicy patterns. Per-block buffering with LiteLLM judge calls. Structured JSON protocol (`pass`/`replace`) with JSON mode.

**Tech Stack:** Python 3.13, LiteLLM (`acompletion`), Pydantic config, pytest

**Spec:** `.claude/specs/simple-llm-policy.md`

---

### Task 1: Judge Utilities Module

Create the judge-calling utilities for SimpleLLMPolicy, separate from the policy itself. This module handles prompt construction, LiteLLM calls, and response parsing.

**Files:**
- Create: `src/luthien_proxy/policies/simple_llm_utils.py`
- Test: `tests/unit_tests/policies/test_simple_llm_utils.py`

**Step 1: Write the failing tests**

Create `tests/unit_tests/policies/test_simple_llm_utils.py`:

```python
"""Unit tests for simple_llm_utils module."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from luthien_proxy.policies.simple_llm_utils import (
    BlockDescriptor,
    JudgeAction,
    ReplacementBlock,
    SimpleLLMJudgeConfig,
    build_judge_prompt,
    call_simple_llm_judge,
    parse_judge_action,
)


class TestParseJudgeAction:
    """Test parsing judge LLM responses into JudgeAction."""

    def test_parse_pass_action(self):
        raw = '{"action": "pass"}'
        result = parse_judge_action(raw)
        assert result.action == "pass"
        assert result.blocks is None

    def test_parse_replace_with_text(self):
        raw = json.dumps({
            "action": "replace",
            "blocks": [{"type": "text", "text": "Modified content"}],
        })
        result = parse_judge_action(raw)
        assert result.action == "replace"
        assert len(result.blocks) == 1
        assert result.blocks[0].type == "text"
        assert result.blocks[0].text == "Modified content"

    def test_parse_replace_with_tool_use(self):
        raw = json.dumps({
            "action": "replace",
            "blocks": [{"type": "tool_use", "name": "read_file", "input": {"path": "/tmp"}}],
        })
        result = parse_judge_action(raw)
        assert result.action == "replace"
        assert result.blocks[0].type == "tool_use"
        assert result.blocks[0].name == "read_file"
        assert result.blocks[0].input == {"path": "/tmp"}

    def test_parse_replace_multiple_blocks(self):
        raw = json.dumps({
            "action": "replace",
            "blocks": [
                {"type": "text", "text": "Here's what I found:"},
                {"type": "tool_use", "name": "safe_read", "input": {"path": "/tmp/safe.txt"}},
            ],
        })
        result = parse_judge_action(raw)
        assert result.action == "replace"
        assert len(result.blocks) == 2

    def test_parse_fenced_json(self):
        raw = '```json\n{"action": "pass"}\n```'
        result = parse_judge_action(raw)
        assert result.action == "pass"

    def test_parse_invalid_json_raises(self):
        with pytest.raises(ValueError, match="JSON parsing failed"):
            parse_judge_action("not json")

    def test_parse_missing_action_raises(self):
        with pytest.raises(ValueError, match="missing.*action"):
            parse_judge_action('{"blocks": []}')

    def test_parse_invalid_action_raises(self):
        with pytest.raises(ValueError, match="invalid action"):
            parse_judge_action('{"action": "delete"}')

    def test_parse_replace_missing_blocks_raises(self):
        with pytest.raises(ValueError, match="missing.*blocks"):
            parse_judge_action('{"action": "replace"}')

    def test_parse_replace_empty_blocks_raises(self):
        with pytest.raises(ValueError, match="empty.*blocks"):
            parse_judge_action('{"action": "replace", "blocks": []}')

    def test_parse_block_missing_type_raises(self):
        raw = json.dumps({"action": "replace", "blocks": [{"text": "no type"}]})
        with pytest.raises(ValueError, match="missing.*type"):
            parse_judge_action(raw)

    def test_parse_text_block_missing_text_raises(self):
        raw = json.dumps({"action": "replace", "blocks": [{"type": "text"}]})
        with pytest.raises(ValueError, match="missing.*text"):
            parse_judge_action(raw)

    def test_parse_tool_block_missing_name_raises(self):
        raw = json.dumps({"action": "replace", "blocks": [{"type": "tool_use", "input": {}}]})
        with pytest.raises(ValueError, match="missing.*name"):
            parse_judge_action(raw)


class TestBuildJudgePrompt:
    """Test judge prompt construction."""

    def test_basic_prompt_structure(self):
        block = BlockDescriptor(type="text", content="Hello world")
        messages = build_judge_prompt(
            instructions="Remove greetings",
            current_block=block,
            previous_blocks=[],
        )
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert "Remove greetings" in messages[0]["content"]
        assert messages[1]["role"] == "user"
        assert "Hello world" in messages[1]["content"]

    def test_prompt_includes_previous_blocks(self):
        prev = BlockDescriptor(type="text", content="Previous text")
        current = BlockDescriptor(type="tool_use", content='{"name": "read", "input": {}}')
        messages = build_judge_prompt(
            instructions="Block file reads",
            current_block=current,
            previous_blocks=[prev],
        )
        user_msg = messages[1]["content"]
        assert "Previous text" in user_msg
        assert "read" in user_msg

    def test_prompt_includes_json_schema(self):
        block = BlockDescriptor(type="text", content="test")
        messages = build_judge_prompt(
            instructions="test",
            current_block=block,
            previous_blocks=[],
        )
        system_msg = messages[0]["content"]
        assert '"action"' in system_msg
        assert '"pass"' in system_msg
        assert '"replace"' in system_msg


class TestCallSimpleLLMJudge:
    """Test the LiteLLM judge call."""

    @pytest.mark.asyncio
    async def test_call_returns_pass(self):
        config = SimpleLLMJudgeConfig(model="test-model", instructions="test")
        block = BlockDescriptor(type="text", content="Hello")

        mock_response = AsyncMock()
        mock_response.choices = [AsyncMock()]
        mock_response.choices[0].message.content = '{"action": "pass"}'

        with patch("luthien_proxy.policies.simple_llm_utils.acompletion", return_value=mock_response):
            result = await call_simple_llm_judge(config, block, [])

        assert result.action == "pass"

    @pytest.mark.asyncio
    async def test_call_returns_replace(self):
        config = SimpleLLMJudgeConfig(model="test-model", instructions="test")
        block = BlockDescriptor(type="text", content="Hello")

        mock_response = AsyncMock()
        mock_response.choices = [AsyncMock()]
        mock_response.choices[0].message.content = json.dumps({
            "action": "replace",
            "blocks": [{"type": "text", "text": "Hi"}],
        })

        with patch("luthien_proxy.policies.simple_llm_utils.acompletion", return_value=mock_response):
            result = await call_simple_llm_judge(config, block, [])

        assert result.action == "replace"
        assert result.blocks[0].text == "Hi"

    @pytest.mark.asyncio
    async def test_call_propagates_litellm_error(self):
        config = SimpleLLMJudgeConfig(model="test-model", instructions="test")
        block = BlockDescriptor(type="text", content="Hello")

        with patch("luthien_proxy.policies.simple_llm_utils.acompletion", side_effect=RuntimeError("API down")):
            with pytest.raises(RuntimeError, match="API down"):
                await call_simple_llm_judge(config, block, [])

    @pytest.mark.asyncio
    async def test_call_uses_json_mode(self):
        config = SimpleLLMJudgeConfig(model="test-model", instructions="test")
        block = BlockDescriptor(type="text", content="Hello")

        mock_response = AsyncMock()
        mock_response.choices = [AsyncMock()]
        mock_response.choices[0].message.content = '{"action": "pass"}'

        with patch("luthien_proxy.policies.simple_llm_utils.acompletion", return_value=mock_response) as mock_call:
            await call_simple_llm_judge(config, block, [])

        call_kwargs = mock_call.call_args.kwargs
        assert call_kwargs.get("response_format") == {"type": "json_object"}
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit_tests/policies/test_simple_llm_utils.py -v`
Expected: FAIL with import errors

**Step 3: Write the implementation**

Create `src/luthien_proxy/policies/simple_llm_utils.py`:

```python
"""Utilities for SimpleLLMPolicy judge LLM calls.

Handles prompt construction, LiteLLM judge calls, and response parsing
for the SimpleLLMPolicy's structured JSON protocol.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from litellm import acompletion
from pydantic import BaseModel, Field

from luthien_proxy.policies.tool_call_judge_utils import parse_judge_response
from luthien_proxy.settings import get_settings

logger = logging.getLogger(__name__)


class SimpleLLMJudgeConfig(BaseModel):
    """Configuration for SimpleLLMPolicy judge calls."""

    model: str = Field(default="claude-haiku-4-5", description="Judge LLM model identifier")
    api_base: str | None = Field(default=None, description="API base URL for judge model")
    api_key: str | None = Field(
        default=None,
        description="API key for judge model (falls back to env vars)",
        json_schema_extra={"format": "password"},
    )
    instructions: str = Field(description="Plain-English instructions for modifying responses")
    temperature: float = Field(default=0.0, description="Sampling temperature for judge LLM")
    max_tokens: int = Field(default=4096, description="Max output tokens for judge response")
    on_error: str = Field(
        default="pass",
        description="Error behavior: 'pass' (fail-open) or 'block' (fail-secure)",
        pattern="^(pass|block)$",
    )

    model_config = {"frozen": True}


@dataclass(frozen=True)
class BlockDescriptor:
    """Description of a content block for the judge prompt."""

    type: str  # "text" or "tool_use"
    content: str  # text content or JSON-serialized tool call info


@dataclass(frozen=True)
class ReplacementBlock:
    """A single replacement block from the judge."""

    type: str  # "text" or "tool_use"
    text: str | None = None  # for text blocks
    name: str | None = None  # for tool_use blocks
    input: dict[str, Any] | None = None  # for tool_use blocks


@dataclass(frozen=True)
class JudgeAction:
    """Parsed judge LLM response."""

    action: str  # "pass" or "replace"
    blocks: tuple[ReplacementBlock, ...] | None = None


_SYSTEM_PROMPT_TEMPLATE = """You are evaluating response blocks from an AI assistant. Your job is to apply the following instructions to each block:

{instructions}

For each block you evaluate, respond with JSON in one of these formats:

If the block needs NO changes:
{{"action": "pass"}}

If the block should be REPLACED:
{{"action": "replace", "blocks": [
  {{"type": "text", "text": "replacement text"}},
  {{"type": "tool_use", "name": "tool_name", "input": {{...}}}}
]}}

Rules:
- Use "pass" whenever possible to avoid unnecessary regeneration
- The "blocks" array can contain one or more replacement blocks
- Each block must have a "type" field ("text" or "tool_use")
- Text blocks need a "text" field
- Tool use blocks need "name" and "input" fields
- Respond ONLY with valid JSON, no other text"""


def build_judge_prompt(
    instructions: str,
    current_block: BlockDescriptor,
    previous_blocks: list[BlockDescriptor],
) -> list[dict[str, str]]:
    """Build the judge LLM prompt.

    Args:
        instructions: User-configured plain-English instructions
        current_block: The block being evaluated
        previous_blocks: Previously emitted blocks (post-replacement) for context
    """
    system_msg = _SYSTEM_PROMPT_TEMPLATE.format(instructions=instructions)

    user_parts: list[str] = []

    if previous_blocks:
        user_parts.append("Previously emitted blocks:")
        for i, block in enumerate(previous_blocks):
            user_parts.append(f"  [{i}] ({block.type}): {block.content}")
        user_parts.append("")

    user_parts.append(f"Current block to evaluate ({current_block.type}):")
    user_parts.append(current_block.content)
    user_parts.append("")
    user_parts.append("Respond with JSON.")

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": "\n".join(user_parts)},
    ]


def parse_judge_action(raw: str) -> JudgeAction:
    """Parse judge LLM response into a JudgeAction.

    Handles fenced code blocks via the shared parse_judge_response utility.

    Raises:
        ValueError: If response cannot be parsed or is invalid
    """
    data = parse_judge_response(raw)

    if "action" not in data:
        raise ValueError("Judge response missing 'action' field")

    action = data["action"]
    if action not in ("pass", "replace"):
        raise ValueError(f"Judge response has invalid action: {action!r}")

    if action == "pass":
        return JudgeAction(action="pass")

    if "blocks" not in data:
        raise ValueError("Judge 'replace' response missing 'blocks' field")

    raw_blocks = data["blocks"]
    if not raw_blocks:
        raise ValueError("Judge 'replace' response has empty 'blocks' array")

    parsed_blocks: list[ReplacementBlock] = []
    for raw_block in raw_blocks:
        if "type" not in raw_block:
            raise ValueError(f"Replacement block missing 'type' field: {raw_block}")

        block_type = raw_block["type"]
        if block_type == "text":
            if "text" not in raw_block:
                raise ValueError(f"Text replacement block missing 'text' field: {raw_block}")
            parsed_blocks.append(ReplacementBlock(type="text", text=raw_block["text"]))
        elif block_type == "tool_use":
            if "name" not in raw_block:
                raise ValueError(f"Tool use replacement block missing 'name' field: {raw_block}")
            parsed_blocks.append(ReplacementBlock(
                type="tool_use",
                name=raw_block["name"],
                input=raw_block.get("input", {}),
            ))
        else:
            raise ValueError(f"Unknown replacement block type: {block_type!r}")

    return JudgeAction(action="replace", blocks=tuple(parsed_blocks))


async def call_simple_llm_judge(
    config: SimpleLLMJudgeConfig,
    current_block: BlockDescriptor,
    previous_blocks: list[BlockDescriptor],
) -> JudgeAction:
    """Call the judge LLM and return the parsed action.

    Args:
        config: Judge configuration
        current_block: Block to evaluate
        previous_blocks: Previously emitted blocks for context

    Returns:
        Parsed JudgeAction

    Raises:
        RuntimeError/ValueError: If LLM call fails or response is unparseable
    """
    messages = build_judge_prompt(config.instructions, current_block, previous_blocks)

    settings = get_settings()
    resolved_api_key = config.api_key or settings.litellm_master_key or None

    kwargs: dict[str, Any] = {
        "model": config.model,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    if config.api_base:
        kwargs["api_base"] = config.api_base
    if resolved_api_key:
        kwargs["api_key"] = resolved_api_key

    response = await acompletion(**kwargs)

    content = response.choices[0].message.content
    if not content or not isinstance(content, str):
        raise ValueError("Judge response content is empty or not a string")

    return parse_judge_action(content)


__all__ = [
    "BlockDescriptor",
    "JudgeAction",
    "ReplacementBlock",
    "SimpleLLMJudgeConfig",
    "build_judge_prompt",
    "call_simple_llm_judge",
    "parse_judge_action",
]
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit_tests/policies/test_simple_llm_utils.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/luthien_proxy/policies/simple_llm_utils.py tests/unit_tests/policies/test_simple_llm_utils.py
git commit -m "feat: add SimpleLLMPolicy judge utilities"
```

---

### Task 2: SimpleLLMPolicy — Anthropic Non-Streaming

Implement the policy class with Anthropic non-streaming support first (simplest path).

**Files:**
- Create: `src/luthien_proxy/policies/simple_llm_policy.py`
- Create: `tests/unit_tests/policies/test_simple_llm_policy.py`

**Step 1: Write the failing tests**

Create `tests/unit_tests/policies/test_simple_llm_policy.py`:

```python
"""Unit tests for SimpleLLMPolicy."""

from __future__ import annotations

import json
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from luthien_proxy.llm.types.anthropic import AnthropicRequest, AnthropicResponse
from luthien_proxy.policies import PolicyContext
from luthien_proxy.policies.simple_llm_policy import SimpleLLMPolicy
from luthien_proxy.policies.simple_llm_utils import JudgeAction, ReplacementBlock


def make_policy(instructions: str = "test instructions", on_error: str = "pass") -> SimpleLLMPolicy:
    return SimpleLLMPolicy(config={"instructions": instructions, "on_error": on_error, "model": "test-model"})


def make_context(transaction_id: str = "test-tx") -> PolicyContext:
    return PolicyContext.for_testing(transaction_id=transaction_id)


def make_anthropic_response(content: list[dict[str, Any]]) -> AnthropicResponse:
    return cast(AnthropicResponse, {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": "test-model",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    })


PASS_ACTION = JudgeAction(action="pass")


def replace_text(text: str) -> JudgeAction:
    return JudgeAction(action="replace", blocks=(ReplacementBlock(type="text", text=text),))


def replace_tool(name: str, input: dict) -> JudgeAction:
    return JudgeAction(action="replace", blocks=(ReplacementBlock(type="tool_use", name=name, input=input),))


class TestAnthropicNonStreaming:
    """Test Anthropic non-streaming response processing."""

    @pytest.mark.asyncio
    async def test_pass_through_text(self):
        policy = make_policy()
        ctx = make_context()
        response = make_anthropic_response([{"type": "text", "text": "Hello world"}])

        with patch("luthien_proxy.policies.simple_llm_policy.call_simple_llm_judge", return_value=PASS_ACTION):
            result = await policy.on_anthropic_response(response, ctx)

        assert result["content"][0]["text"] == "Hello world"

    @pytest.mark.asyncio
    async def test_replace_text_with_text(self):
        policy = make_policy()
        ctx = make_context()
        response = make_anthropic_response([{"type": "text", "text": "Great question! Here's the answer."}])

        with patch(
            "luthien_proxy.policies.simple_llm_policy.call_simple_llm_judge",
            return_value=replace_text("Here's the answer."),
        ):
            result = await policy.on_anthropic_response(response, ctx)

        assert result["content"][0]["type"] == "text"
        assert result["content"][0]["text"] == "Here's the answer."

    @pytest.mark.asyncio
    async def test_replace_tool_with_text(self):
        policy = make_policy()
        ctx = make_context()
        response = make_anthropic_response([
            {"type": "tool_use", "id": "tu_1", "name": "rm_rf", "input": {"path": "/"}},
        ])
        # Original has stop_reason=tool_use
        response = cast(AnthropicResponse, {**response, "stop_reason": "tool_use"})

        with patch(
            "luthien_proxy.policies.simple_llm_policy.call_simple_llm_judge",
            return_value=replace_text("I can't delete files."),
        ):
            result = await policy.on_anthropic_response(response, ctx)

        assert result["content"][0]["type"] == "text"
        assert result["content"][0]["text"] == "I can't delete files."
        # stop_reason should be corrected
        assert result["stop_reason"] == "end_turn"

    @pytest.mark.asyncio
    async def test_replace_text_with_tool(self):
        policy = make_policy()
        ctx = make_context()
        response = make_anthropic_response([{"type": "text", "text": "Let me read that file."}])

        with patch(
            "luthien_proxy.policies.simple_llm_policy.call_simple_llm_judge",
            return_value=replace_tool("read_file", {"path": "/tmp/test.txt"}),
        ):
            result = await policy.on_anthropic_response(response, ctx)

        assert result["content"][0]["type"] == "tool_use"
        assert result["content"][0]["name"] == "read_file"
        # stop_reason should be corrected
        assert result["stop_reason"] == "tool_use"

    @pytest.mark.asyncio
    async def test_multi_block_pass_through(self):
        policy = make_policy()
        ctx = make_context()
        response = make_anthropic_response([
            {"type": "text", "text": "Block 1"},
            {"type": "text", "text": "Block 2"},
        ])

        with patch(
            "luthien_proxy.policies.simple_llm_policy.call_simple_llm_judge",
            return_value=PASS_ACTION,
        ):
            result = await policy.on_anthropic_response(response, ctx)

        assert len(result["content"]) == 2
        assert result["content"][0]["text"] == "Block 1"
        assert result["content"][1]["text"] == "Block 2"

    @pytest.mark.asyncio
    async def test_replace_with_multiple_blocks(self):
        """Judge replaces one block with two blocks."""
        policy = make_policy()
        ctx = make_context()
        response = make_anthropic_response([{"type": "text", "text": "Original"}])

        multi_replace = JudgeAction(
            action="replace",
            blocks=(
                ReplacementBlock(type="text", text="Part 1"),
                ReplacementBlock(type="text", text="Part 2"),
            ),
        )
        with patch(
            "luthien_proxy.policies.simple_llm_policy.call_simple_llm_judge",
            return_value=multi_replace,
        ):
            result = await policy.on_anthropic_response(response, ctx)

        assert len(result["content"]) == 2
        assert result["content"][0]["text"] == "Part 1"
        assert result["content"][1]["text"] == "Part 2"

    @pytest.mark.asyncio
    async def test_error_fail_open(self):
        policy = make_policy(on_error="pass")
        ctx = make_context()
        response = make_anthropic_response([{"type": "text", "text": "Original"}])

        with patch(
            "luthien_proxy.policies.simple_llm_policy.call_simple_llm_judge",
            side_effect=RuntimeError("API down"),
        ):
            result = await policy.on_anthropic_response(response, ctx)

        assert result["content"][0]["text"] == "Original"

    @pytest.mark.asyncio
    async def test_error_fail_secure(self):
        policy = make_policy(on_error="block")
        ctx = make_context()
        response = make_anthropic_response([{"type": "text", "text": "Original"}])

        with patch(
            "luthien_proxy.policies.simple_llm_policy.call_simple_llm_judge",
            side_effect=RuntimeError("API down"),
        ):
            result = await policy.on_anthropic_response(response, ctx)

        # Block should be dropped
        assert len(result["content"]) == 0

    @pytest.mark.asyncio
    async def test_accumulated_context_uses_replacements(self):
        """Second block should see the replaced version of the first block."""
        policy = make_policy()
        ctx = make_context()
        response = make_anthropic_response([
            {"type": "text", "text": "Block 1 original"},
            {"type": "text", "text": "Block 2"},
        ])

        call_count = 0

        async def mock_judge(config, current_block, previous_blocks):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return replace_text("Block 1 replaced")
            # Second call should see the replacement in context
            assert len(previous_blocks) == 1
            assert "Block 1 replaced" in previous_blocks[0].content
            return PASS_ACTION

        with patch(
            "luthien_proxy.policies.simple_llm_policy.call_simple_llm_judge",
            side_effect=mock_judge,
        ):
            result = await policy.on_anthropic_response(response, ctx)

        assert result["content"][0]["text"] == "Block 1 replaced"
        assert result["content"][1]["text"] == "Block 2"


class TestFreeze:
    """Test freeze_configured_state validation."""

    def test_freeze_passes(self):
        policy = make_policy()
        policy.freeze_configured_state()  # Should not raise
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit_tests/policies/test_simple_llm_policy.py -v`
Expected: FAIL with import errors

**Step 3: Write the implementation**

Create `src/luthien_proxy/policies/simple_llm_policy.py`:

```python
"""SimpleLLMPolicy - LLM-based response block modification.

Applies plain-English instructions to each response content block using a
configurable judge LLM. Supports pass-through, text replacement, tool call
replacement, and cross-type replacement (tool->text, text->tool).

Example config:
    policy:
      class: "luthien_proxy.policies.simple_llm_policy:SimpleLLMPolicy"
      config:
        config:
          model: "claude-haiku-4-5"
          instructions: "Remove sycophantic language."
          on_error: "pass"
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

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

from luthien_proxy.policies.simple_llm_utils import (
    BlockDescriptor,
    JudgeAction,
    ReplacementBlock,
    SimpleLLMJudgeConfig,
    call_simple_llm_judge,
)
from luthien_proxy.policy_core import (
    AnthropicExecutionInterface,
    AnthropicPolicyEmission,
    AnthropicPolicyIOProtocol,
    BasePolicy,
    OpenAIPolicyInterface,
    create_finish_chunk,
    create_text_chunk,
    create_tool_call_chunk,
)
from luthien_proxy.policy_core.streaming_utils import (
    get_last_ingress_chunk,
    send_chunk,
    send_text,
    send_tool_call,
)
from luthien_proxy.settings import get_settings
from luthien_proxy.streaming.stream_blocks import ContentStreamBlock, ToolCallStreamBlock

if TYPE_CHECKING:
    from litellm.types.utils import ModelResponse

    from luthien_proxy.llm.types import Request
    from luthien_proxy.llm.types.anthropic import AnthropicRequest, AnthropicResponse
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext

logger = logging.getLogger(__name__)


@dataclass
class _SimpleLLMAnthropicState:
    """Request-scoped state for Anthropic streaming."""

    text_buffer: dict[int, str] = field(default_factory=dict)
    tool_buffer: dict[int, _BufferedToolUse] = field(default_factory=dict)
    emitted_blocks: list[BlockDescriptor] = field(default_factory=list)
    original_had_tool_use: bool = False
    all_tools_replaced: bool = True


@dataclass
class _BufferedToolUse:
    id: str
    name: str
    input_json: str = ""


@dataclass
class _SimpleLLMOpenAIState:
    """Request-scoped state for OpenAI streaming."""

    emitted_blocks: list[BlockDescriptor] = field(default_factory=list)
    original_had_tool_use: bool = False
    all_tools_replaced: bool = True
    buffered_tool_calls: dict[int, ToolCallStreamBlock] = field(default_factory=dict)


class SimpleLLMPolicy(BasePolicy, OpenAIPolicyInterface, AnthropicExecutionInterface):
    """Policy that applies plain-English instructions to response blocks via a judge LLM.

    Each content block (text or tool_use) is evaluated by a judge LLM which can
    either pass it through unchanged or replace it with new content of any type.
    """

    @property
    def short_policy_name(self) -> str:
        return "SimpleLLM"

    def __init__(self, config: SimpleLLMJudgeConfig | dict[str, Any] | None = None):
        self.config = self._init_config(config, SimpleLLMJudgeConfig)

        settings = get_settings()
        resolved_api_key = self.config.api_key or settings.litellm_master_key or None

        self._judge_config = SimpleLLMJudgeConfig(
            model=self.config.model,
            api_base=self.config.api_base,
            api_key=resolved_api_key,
            instructions=self.config.instructions,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            on_error=self.config.on_error,
        )

        logger.info(f"SimpleLLMPolicy initialized: model={self._judge_config.model}")

    # ========================================================================
    # Shared helpers
    # ========================================================================

    def _block_descriptor_from_text(self, text: str) -> BlockDescriptor:
        return BlockDescriptor(type="text", content=text)

    def _block_descriptor_from_tool(self, name: str, input_data: Any) -> BlockDescriptor:
        return BlockDescriptor(
            type="tool_use",
            content=json.dumps({"name": name, "input": input_data}),
        )

    def _replacement_to_anthropic_block(self, block: ReplacementBlock, index: int) -> dict[str, Any]:
        if block.type == "text":
            return {"type": "text", "text": block.text}
        elif block.type == "tool_use":
            return {
                "type": "tool_use",
                "id": f"toolu_{uuid4().hex[:24]}",
                "name": block.name,
                "input": block.input or {},
            }
        raise ValueError(f"Unknown block type: {block.type}")

    def _block_descriptor_from_replacement(self, block: ReplacementBlock) -> BlockDescriptor:
        if block.type == "text":
            return BlockDescriptor(type="text", content=block.text or "")
        return BlockDescriptor(
            type="tool_use",
            content=json.dumps({"name": block.name, "input": block.input or {}}),
        )

    async def _judge_block(
        self,
        block_descriptor: BlockDescriptor,
        emitted_blocks: list[BlockDescriptor],
        context: "PolicyContext",
    ) -> JudgeAction | None:
        """Call judge, return action or None on error (caller handles on_error)."""
        try:
            return await call_simple_llm_judge(self._judge_config, block_descriptor, emitted_blocks)
        except Exception as exc:
            logger.error(f"SimpleLLM judge failed: {exc}", exc_info=True)
            context.record_event(
                "policy.simple_llm.judge_failed",
                {"summary": f"Judge failed: {exc}", "error": str(exc)},
            )
            return None

    def _correct_stop_reason(
        self, response: dict[str, Any], content: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Fix stop_reason based on actual content block types."""
        has_tool_use = any(b.get("type") == "tool_use" for b in content)
        has_text_only = not has_tool_use and any(b.get("type") == "text" for b in content)

        current_reason = response.get("stop_reason")
        if has_tool_use and current_reason != "tool_use":
            response = {**response, "stop_reason": "tool_use"}
        elif has_text_only and current_reason == "tool_use":
            response = {**response, "stop_reason": "end_turn"}

        return response

    # ========================================================================
    # Anthropic non-streaming
    # ========================================================================

    async def on_anthropic_response(
        self, response: "AnthropicResponse", context: "PolicyContext"
    ) -> "AnthropicResponse":
        content = response.get("content", [])
        if not content:
            return response

        emitted_blocks: list[BlockDescriptor] = []
        new_content: list[dict[str, Any]] = []

        for i, block in enumerate(content):
            if not isinstance(block, dict):
                new_content.append(block)
                continue

            block_type = block.get("type")

            if block_type == "text":
                descriptor = self._block_descriptor_from_text(block.get("text", ""))
            elif block_type == "tool_use":
                descriptor = self._block_descriptor_from_tool(block.get("name", ""), block.get("input", {}))
            else:
                new_content.append(block)
                continue

            action = await self._judge_block(descriptor, emitted_blocks, context)

            if action is None:
                # Judge error
                if self._judge_config.on_error == "pass":
                    new_content.append(block)
                    emitted_blocks.append(descriptor)
                # on_error == "block": drop the block
                continue

            if action.action == "pass":
                new_content.append(block)
                emitted_blocks.append(descriptor)
                context.record_event(
                    "policy.simple_llm.block_passed",
                    {"summary": f"Block {i} ({block_type}) passed", "index": i},
                )
            else:
                # Replace
                for replacement in action.blocks:
                    anthropic_block = self._replacement_to_anthropic_block(replacement, i)
                    new_content.append(anthropic_block)
                    emitted_blocks.append(self._block_descriptor_from_replacement(replacement))
                context.record_event(
                    "policy.simple_llm.block_replaced",
                    {
                        "summary": f"Block {i} ({block_type}) replaced with {len(action.blocks)} block(s)",
                        "index": i,
                        "replacement_count": len(action.blocks),
                    },
                )

        modified_response = cast("AnthropicResponse", {**response, "content": new_content})
        modified_response = cast("AnthropicResponse", self._correct_stop_reason(modified_response, new_content))
        return modified_response

    # ========================================================================
    # Anthropic execution interface
    # ========================================================================

    async def on_anthropic_request(
        self, request: "AnthropicRequest", context: "PolicyContext"
    ) -> "AnthropicRequest":
        return request

    def run_anthropic(
        self, io: AnthropicPolicyIOProtocol, context: "PolicyContext"
    ) -> AsyncIterator[AnthropicPolicyEmission]:
        async def _run() -> AsyncIterator[AnthropicPolicyEmission]:
            request = io.request

            if request.get("stream", False):
                async for event in io.stream(request):
                    emitted_events = await self.on_anthropic_stream_event(event, context)
                    for emitted_event in emitted_events:
                        yield emitted_event
                return

            response = await io.complete(request)
            yield await self.on_anthropic_response(response, context)

        return _run()

    # ========================================================================
    # Anthropic streaming (Task 3 will fill these in)
    # ========================================================================

    def _anthropic_state(self, context: "PolicyContext") -> _SimpleLLMAnthropicState:
        return context.get_request_state(self, _SimpleLLMAnthropicState, _SimpleLLMAnthropicState)

    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        # Placeholder - Task 3 implements this
        return [event]

    async def on_anthropic_streaming_policy_complete(self, context: "PolicyContext") -> None:
        context.pop_request_state(self, _SimpleLLMAnthropicState)

    # ========================================================================
    # OpenAI non-streaming (Task 4 will fill these in)
    # ========================================================================

    async def on_openai_request(self, request: "Request", context: "PolicyContext") -> "Request":
        return request

    async def on_openai_response(
        self, response: "ModelResponse", context: "PolicyContext"
    ) -> "ModelResponse":
        # Placeholder - Task 4 implements this
        return response

    # ========================================================================
    # OpenAI streaming (Task 5 will fill these in)
    # ========================================================================

    async def on_chunk_received(self, ctx: "StreamingPolicyContext") -> None:
        pass

    async def on_content_delta(self, ctx: "StreamingPolicyContext") -> None:
        pass

    async def on_content_complete(self, ctx: "StreamingPolicyContext") -> None:
        pass

    async def on_tool_call_delta(self, ctx: "StreamingPolicyContext") -> None:
        pass

    async def on_tool_call_complete(self, ctx: "StreamingPolicyContext") -> None:
        pass

    async def on_finish_reason(self, ctx: "StreamingPolicyContext") -> None:
        pass

    async def on_stream_complete(self, ctx: "StreamingPolicyContext") -> None:
        pass

    async def on_streaming_policy_complete(self, ctx: "StreamingPolicyContext") -> None:
        ctx.policy_ctx.pop_request_state(self, _SimpleLLMOpenAIState)


__all__ = ["SimpleLLMPolicy"]
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit_tests/policies/test_simple_llm_policy.py -v`
Expected: All PASS

**Step 5: Run dev_checks to verify nothing is broken**

Run: `./scripts/dev_checks.sh`
Expected: PASS

**Step 6: Commit**

```bash
git add src/luthien_proxy/policies/simple_llm_policy.py tests/unit_tests/policies/test_simple_llm_policy.py
git commit -m "feat: add SimpleLLMPolicy with Anthropic non-streaming support"
```

---

### Task 3: Anthropic Streaming Support

Implement Anthropic streaming: buffer text/tool_use blocks, judge on block completion, emit transformed events.

**Files:**
- Modify: `src/luthien_proxy/policies/simple_llm_policy.py` (fill in `on_anthropic_stream_event` and helpers)
- Modify: `tests/unit_tests/policies/test_simple_llm_policy.py` (add streaming tests)

**Step 1: Write failing tests**

Add to `tests/unit_tests/policies/test_simple_llm_policy.py`:

```python
from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    RawMessageStartEvent,
    RawMessageStopEvent,
    TextBlock,
    TextDelta,
    ToolUseBlock,
    Usage,
)


class TestAnthropicStreaming:
    """Test Anthropic streaming event processing."""

    @pytest.mark.asyncio
    async def test_text_block_pass_through(self):
        policy = make_policy()
        ctx = make_context()

        # content_block_start with text
        start = RawContentBlockStartEvent(
            type="content_block_start", index=0, content_block=TextBlock(type="text", text="")
        )
        events = await policy.on_anthropic_stream_event(start, ctx)
        assert len(events) == 1  # start event passes through

        # text deltas are buffered
        delta = RawContentBlockDeltaEvent(
            type="content_block_delta", index=0, delta=TextDelta(type="text_delta", text="Hello world")
        )
        events = await policy.on_anthropic_stream_event(delta, ctx)
        assert len(events) == 0  # buffered

        # content_block_stop triggers judge
        stop = RawContentBlockStopEvent(type="content_block_stop", index=0)
        with patch(
            "luthien_proxy.policies.simple_llm_policy.call_simple_llm_judge",
            return_value=PASS_ACTION,
        ):
            events = await policy.on_anthropic_stream_event(stop, ctx)

        # Should emit: text_delta with original content + stop
        assert len(events) == 2
        assert isinstance(events[0], RawContentBlockDeltaEvent)
        assert events[0].delta.text == "Hello world"
        assert isinstance(events[1], RawContentBlockStopEvent)

    @pytest.mark.asyncio
    async def test_text_block_replaced(self):
        policy = make_policy()
        ctx = make_context()

        start = RawContentBlockStartEvent(
            type="content_block_start", index=0, content_block=TextBlock(type="text", text="")
        )
        await policy.on_anthropic_stream_event(start, ctx)

        delta = RawContentBlockDeltaEvent(
            type="content_block_delta", index=0, delta=TextDelta(type="text_delta", text="Bad content")
        )
        await policy.on_anthropic_stream_event(delta, ctx)

        stop = RawContentBlockStopEvent(type="content_block_stop", index=0)
        with patch(
            "luthien_proxy.policies.simple_llm_policy.call_simple_llm_judge",
            return_value=replace_text("Good content"),
        ):
            events = await policy.on_anthropic_stream_event(stop, ctx)

        # Should emit: text_delta with replacement + stop
        assert len(events) == 2
        assert events[0].delta.text == "Good content"

    @pytest.mark.asyncio
    async def test_tool_use_replaced_with_text(self):
        policy = make_policy()
        ctx = make_context()

        # tool_use start - buffered, not emitted
        tool_block = ToolUseBlock(type="tool_use", id="tu_1", name="rm_rf", input={})
        start = RawContentBlockStartEvent(type="content_block_start", index=0, content_block=tool_block)
        events = await policy.on_anthropic_stream_event(start, ctx)
        assert len(events) == 0  # tool_use start is buffered

        # JSON delta - buffered
        json_delta = InputJSONDelta(type="input_json_delta", partial_json='{"path": "/"}')
        delta = RawContentBlockDeltaEvent(type="content_block_delta", index=0, delta=json_delta)
        events = await policy.on_anthropic_stream_event(delta, ctx)
        assert len(events) == 0

        # Stop triggers judge -> replace with text
        stop = RawContentBlockStopEvent(type="content_block_stop", index=0)
        with patch(
            "luthien_proxy.policies.simple_llm_policy.call_simple_llm_judge",
            return_value=replace_text("I can't do that."),
        ):
            events = await policy.on_anthropic_stream_event(stop, ctx)

        # Should emit: text block start + text delta + stop
        assert len(events) == 3
        assert isinstance(events[0], RawContentBlockStartEvent)
        assert isinstance(events[0].content_block, TextBlock)
        assert isinstance(events[1], RawContentBlockDeltaEvent)
        assert events[1].delta.text == "I can't do that."
        assert isinstance(events[2], RawContentBlockStopEvent)

    @pytest.mark.asyncio
    async def test_non_content_events_pass_through(self):
        """message_start, message_delta, message_stop pass through unchanged."""
        policy = make_policy()
        ctx = make_context()

        msg_start = RawMessageStartEvent(
            type="message_start",
            message=cast(Any, {"id": "msg_1", "type": "message", "role": "assistant",
                               "content": [], "model": "test", "stop_reason": None,
                               "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}}),
        )
        events = await policy.on_anthropic_stream_event(msg_start, ctx)
        assert len(events) == 1
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit_tests/policies/test_simple_llm_policy.py::TestAnthropicStreaming -v`
Expected: FAIL (streaming events pass through without buffering/judging)

**Step 3: Implement Anthropic streaming**

Replace the `on_anthropic_stream_event` placeholder and add helper methods in `simple_llm_policy.py`:

```python
    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        if isinstance(event, RawContentBlockStartEvent):
            return self._handle_block_start(event, context)

        if isinstance(event, RawContentBlockDeltaEvent):
            return self._handle_block_delta(event, context)

        if isinstance(event, RawContentBlockStopEvent):
            return await self._handle_block_stop(event, context)

        return [event]

    def _handle_block_start(
        self, event: RawContentBlockStartEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        state = self._anthropic_state(context)
        index = event.index
        content_block = event.content_block

        if isinstance(content_block, ToolUseBlock):
            state.tool_buffer[index] = _BufferedToolUse(id=content_block.id, name=content_block.name)
            state.original_had_tool_use = True
            return []  # Buffer tool_use start

        if hasattr(content_block, "type") and content_block.type == "text":
            state.text_buffer[index] = ""
            return [event]  # Text start passes through

        return [event]

    def _handle_block_delta(
        self, event: RawContentBlockDeltaEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        state = self._anthropic_state(context)
        index = event.index
        delta = event.delta

        if isinstance(delta, TextDelta) and index in state.text_buffer:
            state.text_buffer[index] += delta.text
            return []  # Buffer text

        if isinstance(delta, InputJSONDelta) and index in state.tool_buffer:
            state.tool_buffer[index].input_json += delta.partial_json
            return []  # Buffer tool JSON

        return [event]

    async def _handle_block_stop(
        self, event: RawContentBlockStopEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        state = self._anthropic_state(context)
        index = event.index

        if index in state.text_buffer:
            content = state.text_buffer.pop(index)
            descriptor = self._block_descriptor_from_text(content)
            action = await self._judge_block(descriptor, state.emitted_blocks, context)
            return self._emit_anthropic_judged(action, descriptor, content, index, state, event, context, block_type="text")

        if index in state.tool_buffer:
            tool_info = state.tool_buffer.pop(index)
            input_data = json.loads(tool_info.input_json) if tool_info.input_json else {}
            descriptor = self._block_descriptor_from_tool(tool_info.name, input_data)
            action = await self._judge_block(descriptor, state.emitted_blocks, context)
            return self._emit_anthropic_judged(
                action, descriptor, None, index, state, event, context,
                block_type="tool_use", tool_info=tool_info, input_data=input_data,
            )

        return [cast(MessageStreamEvent, event)]

    def _emit_anthropic_judged(
        self,
        action: JudgeAction | None,
        descriptor: BlockDescriptor,
        original_text: str | None,
        index: int,
        state: _SimpleLLMAnthropicState,
        stop_event: RawContentBlockStopEvent,
        context: "PolicyContext",
        block_type: str,
        tool_info: _BufferedToolUse | None = None,
        input_data: dict | None = None,
    ) -> list[MessageStreamEvent]:
        """Emit events based on judge action for a completed block."""

        if action is None:
            # Judge error
            if self._judge_config.on_error == "pass":
                state.emitted_blocks.append(descriptor)
                return self._reconstruct_original_anthropic(
                    block_type, index, stop_event, original_text, tool_info, input_data
                )
            return []  # Drop on error with on_error=block

        if action.action == "pass":
            state.emitted_blocks.append(descriptor)
            return self._reconstruct_original_anthropic(
                block_type, index, stop_event, original_text, tool_info, input_data
            )

        # Replace
        events: list[MessageStreamEvent] = []
        for replacement in action.blocks:
            state.emitted_blocks.append(self._block_descriptor_from_replacement(replacement))
            events.extend(self._replacement_to_anthropic_events(replacement, index))
        events.append(cast(MessageStreamEvent, stop_event))

        if block_type == "tool_use":
            # Track that this tool was replaced
            pass  # all_tools_replaced stays True unless a tool passes through
        return events

    def _reconstruct_original_anthropic(
        self,
        block_type: str,
        index: int,
        stop_event: RawContentBlockStopEvent,
        original_text: str | None,
        tool_info: _BufferedToolUse | None,
        input_data: dict | None,
    ) -> list[MessageStreamEvent]:
        """Reconstruct original block events for pass-through."""
        if block_type == "text":
            text_delta = TextDelta.model_construct(type="text_delta", text=original_text or "")
            delta_event = RawContentBlockDeltaEvent.model_construct(
                type="content_block_delta", index=index, delta=text_delta,
            )
            return [delta_event, cast(MessageStreamEvent, stop_event)]

        # tool_use: need start + delta + stop
        tool_block = ToolUseBlock(type="tool_use", id=tool_info.id, name=tool_info.name, input={})
        start_event = RawContentBlockStartEvent(type="content_block_start", index=index, content_block=tool_block)
        json_str = json.dumps(input_data) if input_data else "{}"
        json_delta = InputJSONDelta(type="input_json_delta", partial_json=json_str)
        delta_event = RawContentBlockDeltaEvent(type="content_block_delta", index=index, delta=json_delta)
        return [
            cast(MessageStreamEvent, start_event),
            cast(MessageStreamEvent, delta_event),
            cast(MessageStreamEvent, stop_event),
        ]

    def _replacement_to_anthropic_events(
        self, block: ReplacementBlock, index: int
    ) -> list[MessageStreamEvent]:
        """Convert a ReplacementBlock to Anthropic streaming events."""
        if block.type == "text":
            text_block = TextBlock(type="text", text="")
            start = RawContentBlockStartEvent(type="content_block_start", index=index, content_block=text_block)
            delta = RawContentBlockDeltaEvent(
                type="content_block_delta", index=index,
                delta=TextDelta(type="text_delta", text=block.text or ""),
            )
            return [cast(MessageStreamEvent, start), cast(MessageStreamEvent, delta)]

        # tool_use
        tool_block = ToolUseBlock(
            type="tool_use",
            id=f"toolu_{uuid4().hex[:24]}",
            name=block.name or "",
            input={},
        )
        start = RawContentBlockStartEvent(type="content_block_start", index=index, content_block=tool_block)
        json_str = json.dumps(block.input or {})
        delta = RawContentBlockDeltaEvent(
            type="content_block_delta", index=index,
            delta=InputJSONDelta(type="input_json_delta", partial_json=json_str),
        )
        return [cast(MessageStreamEvent, start), cast(MessageStreamEvent, delta)]
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit_tests/policies/test_simple_llm_policy.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add -u
git commit -m "feat: add Anthropic streaming support to SimpleLLMPolicy"
```

---

### Task 4: OpenAI Non-Streaming Support

Implement OpenAI non-streaming response processing.

**Files:**
- Modify: `src/luthien_proxy/policies/simple_llm_policy.py`
- Modify: `tests/unit_tests/policies/test_simple_llm_policy.py`

**Step 1: Write failing tests**

Add to `tests/unit_tests/policies/test_simple_llm_policy.py`:

```python
from litellm.types.utils import Choices, Message, ModelResponse as LiteLLMResponse
from luthien_proxy.llm.types import Request
from tests.unit_tests.helpers.litellm_test_utils import make_complete_response


def make_openai_context(transaction_id: str = "test-tx") -> PolicyContext:
    return PolicyContext.for_testing(
        transaction_id=transaction_id,
        request=Request(model="test-model", messages=[{"role": "user", "content": "test"}]),
    )


def make_tool_call_response() -> LiteLLMResponse:
    """Create a non-streaming response with a tool call."""
    from litellm.types.utils import ChatCompletionMessageToolCall, Function
    return LiteLLMResponse(
        id="resp_1",
        created=1234567890,
        model="test-model",
        object="chat.completion",
        choices=[Choices(
            index=0,
            finish_reason="tool_calls",
            message=Message(
                role="assistant",
                content=None,
                tool_calls=[
                    ChatCompletionMessageToolCall(
                        id="call_1",
                        type="function",
                        function=Function(name="rm_rf", arguments='{"path": "/"}'),
                    )
                ],
            ),
        )],
    )


class TestOpenAINonStreaming:
    """Test OpenAI non-streaming response processing."""

    @pytest.mark.asyncio
    async def test_pass_through_text(self):
        policy = make_policy()
        ctx = make_openai_context()
        response = make_complete_response("Hello world")

        with patch("luthien_proxy.policies.simple_llm_policy.call_simple_llm_judge", return_value=PASS_ACTION):
            result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "Hello world"

    @pytest.mark.asyncio
    async def test_replace_text(self):
        policy = make_policy()
        ctx = make_openai_context()
        response = make_complete_response("Great question! Here's the answer.")

        with patch(
            "luthien_proxy.policies.simple_llm_policy.call_simple_llm_judge",
            return_value=replace_text("Here's the answer."),
        ):
            result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "Here's the answer."

    @pytest.mark.asyncio
    async def test_replace_tool_call_with_text(self):
        policy = make_policy()
        ctx = make_openai_context()
        response = make_tool_call_response()

        with patch(
            "luthien_proxy.policies.simple_llm_policy.call_simple_llm_judge",
            return_value=replace_text("I can't delete files."),
        ):
            result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "I can't delete files."
        assert result.choices[0].message.tool_calls is None
        assert result.choices[0].finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_error_fail_open(self):
        policy = make_policy(on_error="pass")
        ctx = make_openai_context()
        response = make_complete_response("Original")

        with patch(
            "luthien_proxy.policies.simple_llm_policy.call_simple_llm_judge",
            side_effect=RuntimeError("down"),
        ):
            result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "Original"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit_tests/policies/test_simple_llm_policy.py::TestOpenAINonStreaming -v`
Expected: FAIL (on_openai_response is a pass-through placeholder)

**Step 3: Implement `on_openai_response`**

Replace the placeholder in `simple_llm_policy.py`:

```python
    async def on_openai_response(
        self, response: "ModelResponse", context: "PolicyContext"
    ) -> "ModelResponse":
        from litellm.types.utils import Choices, ChatCompletionMessageToolCall, Function, Message

        if not response.choices:
            return response

        emitted_blocks: list[BlockDescriptor] = []

        for choice in response.choices:
            if not isinstance(choice, Choices):
                continue

            new_content_parts: list[str] = []
            new_tool_calls: list[ChatCompletionMessageToolCall] = []

            # Process text content
            if isinstance(choice.message.content, str) and choice.message.content:
                descriptor = self._block_descriptor_from_text(choice.message.content)
                action = await self._judge_block(descriptor, emitted_blocks, context)

                if action is None:
                    if self._judge_config.on_error == "pass":
                        new_content_parts.append(choice.message.content)
                        emitted_blocks.append(descriptor)
                elif action.action == "pass":
                    new_content_parts.append(choice.message.content)
                    emitted_blocks.append(descriptor)
                else:
                    for block in action.blocks:
                        if block.type == "text":
                            new_content_parts.append(block.text or "")
                        elif block.type == "tool_use":
                            new_tool_calls.append(ChatCompletionMessageToolCall(
                                id=f"call_{uuid4().hex[:24]}",
                                type="function",
                                function=Function(name=block.name or "", arguments=json.dumps(block.input or {})),
                            ))
                        emitted_blocks.append(self._block_descriptor_from_replacement(block))

            # Process tool calls
            if choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    name = tc.function.name if tc.function else ""
                    args = tc.function.arguments if tc.function else "{}"
                    input_data = json.loads(args) if args else {}
                    descriptor = self._block_descriptor_from_tool(name, input_data)
                    action = await self._judge_block(descriptor, emitted_blocks, context)

                    if action is None:
                        if self._judge_config.on_error == "pass":
                            new_tool_calls.append(tc)
                            emitted_blocks.append(descriptor)
                    elif action.action == "pass":
                        new_tool_calls.append(tc)
                        emitted_blocks.append(descriptor)
                    else:
                        for block in action.blocks:
                            if block.type == "text":
                                new_content_parts.append(block.text or "")
                            elif block.type == "tool_use":
                                new_tool_calls.append(ChatCompletionMessageToolCall(
                                    id=f"call_{uuid4().hex[:24]}",
                                    type="function",
                                    function=Function(name=block.name or "", arguments=json.dumps(block.input or {})),
                                ))
                            emitted_blocks.append(self._block_descriptor_from_replacement(block))

            # Update choice
            choice.message.content = "\n".join(new_content_parts) if new_content_parts else None
            choice.message.tool_calls = new_tool_calls if new_tool_calls else None

            # Fix finish_reason
            if new_tool_calls and not new_content_parts:
                choice.finish_reason = "tool_calls"
            elif new_content_parts and not new_tool_calls:
                choice.finish_reason = "stop"

        return response
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit_tests/policies/test_simple_llm_policy.py -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add -u
git commit -m "feat: add OpenAI non-streaming support to SimpleLLMPolicy"
```

---

### Task 5: OpenAI Streaming Support

Implement OpenAI streaming: buffer content/tool call blocks, judge on completion, emit transformed chunks.

**Files:**
- Modify: `src/luthien_proxy/policies/simple_llm_policy.py`
- Modify: `tests/unit_tests/policies/test_simple_llm_policy.py`

**Step 1: Write failing tests**

Add to the test file:

```python
from unittest.mock import Mock
from tests.unit_tests.helpers.litellm_test_utils import make_streaming_chunk
from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext
from luthien_proxy.streaming.stream_blocks import ContentStreamBlock, ToolCallStreamBlock
from luthien_proxy.streaming.stream_state import StreamState


def create_mock_streaming_ctx(
    transaction_id: str = "test-tx",
    just_completed=None,
    raw_chunks=None,
) -> StreamingPolicyContext:
    ctx = Mock(spec=StreamingPolicyContext)
    ctx.policy_ctx = PolicyContext.for_testing(
        transaction_id=transaction_id,
        request=Request(model="test-model", messages=[{"role": "user", "content": "test"}]),
    )
    ctx.original_streaming_response_state = StreamState()
    ctx.original_streaming_response_state.just_completed = just_completed
    ctx.original_streaming_response_state.raw_chunks = raw_chunks or []
    ctx.egress_queue = Mock()
    ctx.egress_queue.put_nowait = Mock()
    ctx.egress_queue.put = AsyncMock()
    return ctx


class TestOpenAIStreaming:
    """Test OpenAI streaming chunk processing."""

    @pytest.mark.asyncio
    async def test_content_complete_pass_through(self):
        policy = make_policy()
        block = ContentStreamBlock(index=0)
        block.content = "Hello world"
        ctx = create_mock_streaming_ctx(
            just_completed=block,
            raw_chunks=[make_streaming_chunk("Hello world", finish_reason="stop")],
        )

        with patch("luthien_proxy.policies.simple_llm_policy.call_simple_llm_judge", return_value=PASS_ACTION):
            await policy.on_content_complete(ctx)

        # Should have emitted text chunk + finish chunk
        assert ctx.egress_queue.put.call_count >= 1

    @pytest.mark.asyncio
    async def test_content_complete_replaced(self):
        policy = make_policy()
        block = ContentStreamBlock(index=0)
        block.content = "Bad content"
        ctx = create_mock_streaming_ctx(
            just_completed=block,
            raw_chunks=[make_streaming_chunk("Bad content", finish_reason="stop")],
        )

        with patch(
            "luthien_proxy.policies.simple_llm_policy.call_simple_llm_judge",
            return_value=replace_text("Good content"),
        ):
            await policy.on_content_complete(ctx)

        # Check the emitted text
        put_calls = ctx.egress_queue.put.call_args_list
        emitted_chunks = [call.args[0] for call in put_calls]
        text_content = "".join(
            c.choices[0].delta.content for c in emitted_chunks
            if hasattr(c.choices[0], "delta") and c.choices[0].delta.content
        )
        assert "Good content" in text_content

    @pytest.mark.asyncio
    async def test_tool_call_complete_replaced_with_text(self):
        policy = make_policy()
        block = ToolCallStreamBlock(id="call_1", index=0, name="rm_rf", arguments='{"path": "/"}')
        ctx = create_mock_streaming_ctx(
            just_completed=block,
            raw_chunks=[make_streaming_chunk(None, finish_reason="tool_calls")],
        )

        with patch(
            "luthien_proxy.policies.simple_llm_policy.call_simple_llm_judge",
            return_value=replace_text("I can't do that."),
        ):
            await policy.on_tool_call_complete(ctx)

        put_calls = ctx.egress_queue.put.call_args_list
        emitted_chunks = [call.args[0] for call in put_calls]
        text_content = "".join(
            c.choices[0].delta.content for c in emitted_chunks
            if hasattr(c.choices[0], "delta") and c.choices[0].delta.content
        )
        assert "I can't do that." in text_content
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit_tests/policies/test_simple_llm_policy.py::TestOpenAIStreaming -v`
Expected: FAIL

**Step 3: Implement OpenAI streaming methods**

Fill in the placeholder streaming methods in `simple_llm_policy.py`:

```python
    def _openai_state(self, ctx: "StreamingPolicyContext") -> _SimpleLLMOpenAIState:
        return ctx.policy_ctx.get_request_state(self, _SimpleLLMOpenAIState, _SimpleLLMOpenAIState)

    async def on_chunk_received(self, ctx: "StreamingPolicyContext") -> None:
        pass  # Don't auto-forward; specific handlers deal with it

    async def on_content_delta(self, ctx: "StreamingPolicyContext") -> None:
        pass  # Buffered by stream state, emitted on content_complete

    async def on_tool_call_delta(self, ctx: "StreamingPolicyContext") -> None:
        pass  # Buffered by stream state, emitted on tool_call_complete

    async def on_content_complete(self, ctx: "StreamingPolicyContext") -> None:
        block = ctx.original_streaming_response_state.just_completed
        if not isinstance(block, ContentStreamBlock):
            return

        state = self._openai_state(ctx)
        descriptor = self._block_descriptor_from_text(block.content)
        action = await self._judge_block(descriptor, state.emitted_blocks, ctx.policy_ctx)

        if action is None:
            if self._judge_config.on_error == "pass":
                state.emitted_blocks.append(descriptor)
                await send_text(ctx, block.content)
            return

        if action.action == "pass":
            state.emitted_blocks.append(descriptor)
            await send_text(ctx, block.content)
        else:
            for replacement in action.blocks:
                state.emitted_blocks.append(self._block_descriptor_from_replacement(replacement))
                if replacement.type == "text":
                    await send_text(ctx, replacement.text or "")
                elif replacement.type == "tool_use":
                    from litellm.types.utils import ChatCompletionMessageToolCall, Function
                    tc = ChatCompletionMessageToolCall(
                        id=f"call_{uuid4().hex[:24]}",
                        type="function",
                        function=Function(name=replacement.name or "", arguments=json.dumps(replacement.input or {})),
                    )
                    await send_tool_call(ctx, tc)

        # Emit finish_reason after content
        last_chunk = get_last_ingress_chunk(ctx)
        if last_chunk and last_chunk.choices and last_chunk.choices[0].finish_reason:
            finish_chunk = create_finish_chunk(
                finish_reason=last_chunk.choices[0].finish_reason,
                model=last_chunk.model,
                chunk_id=last_chunk.id,
            )
            await send_chunk(ctx, finish_chunk)

    async def on_tool_call_complete(self, ctx: "StreamingPolicyContext") -> None:
        block = ctx.original_streaming_response_state.just_completed
        if not isinstance(block, ToolCallStreamBlock):
            return

        state = self._openai_state(ctx)
        input_data = json.loads(block.arguments) if block.arguments else {}
        descriptor = self._block_descriptor_from_tool(block.name, input_data)
        action = await self._judge_block(descriptor, state.emitted_blocks, ctx.policy_ctx)

        if action is None:
            if self._judge_config.on_error == "pass":
                state.emitted_blocks.append(descriptor)
                await send_tool_call(ctx, block.tool_call)
            return

        if action.action == "pass":
            state.emitted_blocks.append(descriptor)
            await send_tool_call(ctx, block.tool_call)
        else:
            state.original_had_tool_use = True
            for replacement in action.blocks:
                state.emitted_blocks.append(self._block_descriptor_from_replacement(replacement))
                if replacement.type == "text":
                    await send_text(ctx, replacement.text or "")
                elif replacement.type == "tool_use":
                    from litellm.types.utils import ChatCompletionMessageToolCall, Function
                    tc = ChatCompletionMessageToolCall(
                        id=f"call_{uuid4().hex[:24]}",
                        type="function",
                        function=Function(name=replacement.name or "", arguments=json.dumps(replacement.input or {})),
                    )
                    await send_tool_call(ctx, tc)

    async def on_finish_reason(self, ctx: "StreamingPolicyContext") -> None:
        pass  # Handled in on_content_complete and on_stream_complete

    async def on_stream_complete(self, ctx: "StreamingPolicyContext") -> None:
        finish_reason = ctx.original_streaming_response_state.finish_reason
        if not finish_reason:
            return

        # For tool call responses, emit finish_reason
        blocks = ctx.original_streaming_response_state.blocks
        has_tool_calls = any(isinstance(b, ToolCallStreamBlock) for b in blocks)

        if has_tool_calls:
            state = self._openai_state(ctx)
            # Check if all tools were replaced with text
            has_remaining_tools = any(b.type == "tool_use" for b in state.emitted_blocks)
            corrected_reason = "tool_calls" if has_remaining_tools else "stop"

            last_chunk = get_last_ingress_chunk(ctx)
            chunk_id = last_chunk.id if last_chunk else None
            model = last_chunk.model if last_chunk else "luthien-policy"

            finish_chunk = create_finish_chunk(
                finish_reason=corrected_reason,
                model=model,
                chunk_id=chunk_id,
            )
            await send_chunk(ctx, finish_chunk)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit_tests/policies/test_simple_llm_policy.py -v`
Expected: All PASS

**Step 5: Run dev_checks**

Run: `./scripts/dev_checks.sh`
Expected: PASS

**Step 6: Commit**

```bash
git add -u
git commit -m "feat: add OpenAI streaming support to SimpleLLMPolicy"
```

---

### Task 6: Integration Smoke Test & Final Polish

Register the policy, run dev_checks, verify YAML loading works.

**Files:**
- Modify: `src/luthien_proxy/policies/__init__.py` (if needed for registration)
- May modify: `config/policy_config.yaml` (add example comment)

**Step 1: Verify YAML config loading works**

Add a test to `test_simple_llm_policy.py`:

```python
class TestConfigLoading:
    def test_from_dict(self):
        policy = SimpleLLMPolicy(config={
            "model": "claude-haiku-4-5",
            "instructions": "Remove greetings",
            "on_error": "pass",
        })
        assert policy.config.model == "claude-haiku-4-5"
        assert policy.config.instructions == "Remove greetings"

    def test_from_none_raises(self):
        """instructions is required, so None config should fail."""
        with pytest.raises(Exception):
            SimpleLLMPolicy(config=None)

    def test_invalid_on_error(self):
        with pytest.raises(Exception):
            SimpleLLMPolicy(config={"instructions": "test", "on_error": "invalid"})
```

**Step 2: Run test**

Run: `uv run pytest tests/unit_tests/policies/test_simple_llm_policy.py::TestConfigLoading -v`
Expected: PASS

**Step 3: Run full dev_checks**

Run: `./scripts/dev_checks.sh`
Expected: All PASS (format, lint, type check, tests)

**Step 4: Final commit**

```bash
git add -u
git commit -m "feat: SimpleLLMPolicy config validation and final polish"
```

---

### Task 7: Update CHANGELOG and Complete PR

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `dev/OBJECTIVE.md`
- Modify: `dev/NOTES.md`

**Step 1: Update CHANGELOG**

Add entry under the latest section:

```markdown
- **SimpleLLMPolicy**: New policy that applies plain-English instructions to LLM response blocks using a configurable judge LLM. Supports pass-through, text/tool replacement, and cross-type replacement. Works with both OpenAI and Anthropic APIs in streaming and non-streaming modes.
```

**Step 2: Clear dev files**

Clear `dev/OBJECTIVE.md` and `dev/NOTES.md`.

**Step 3: Run dev_checks one final time**

Run: `./scripts/dev_checks.sh`
Expected: PASS

**Step 4: Commit and mark PR ready**

```bash
git add CHANGELOG.md dev/OBJECTIVE.md dev/NOTES.md
git commit -m "chore: update CHANGELOG, clear dev files"
gh pr ready
```
