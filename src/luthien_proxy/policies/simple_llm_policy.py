"""SimpleLLMPolicy - Apply plain-English instructions to LLM response blocks.

This policy evaluates each content block (text or tool_use) in an Anthropic LLM
response against configurable instructions using a judge LLM. The judge can pass
blocks through or replace them with different content, including cross-type
replacement (e.g., replacing a tool_use with text).

Supports Anthropic API format, streaming and non-streaming.

Example config:
    policy:
      class: "luthien_proxy.policies.simple_llm_policy:SimpleLLMPolicy"
      config:
        config:
          model: "claude-haiku-4-5"
          instructions: "Remove any PII from responses"
          on_error: "pass"
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    TextBlock,
    TextDelta,
    ToolUseBlock,
)

from luthien_proxy.credentials import AuthProvider, parse_auth_provider
from luthien_proxy.policies.simple_llm_utils import (
    BlockDescriptor,
    JudgeAction,
    ReplacementBlock,
    SimpleLLMJudgeConfig,
    call_simple_llm_judge,
)
from luthien_proxy.policy_core import (
    AnthropicHookPolicy,
    BasePolicy,
)
from luthien_proxy.settings import get_settings

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicContentBlock,
        AnthropicResponse,
        AnthropicTextBlock,
        JSONObject,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)


@dataclass
class _BufferedToolUse:
    id: str
    name: str
    input_json: str = ""


JUDGE_UNAVAILABLE_WARNING = (
    "\u26a0\ufe0f Safety judge unavailable \u2014 this response was not evaluated by the safety policy."
)

JUDGE_ERROR_BLOCKED_MESSAGE = (
    "\u274c Response blocked: the safety judge encountered an error and the policy requires blocking on failure."
)


def _blocked_tool_message(name: str) -> str:
    return f"[Tool call `{name}` was blocked by policy]"


def _blocked_tool_judge_failed_message(name: str) -> str:
    return f"[Tool call `{name}` blocked: policy evaluation unavailable]"


@dataclass
class _SimpleLLMAnthropicState:
    text_buffer: dict[int, str] = field(default_factory=dict)
    tool_buffer: dict[int, _BufferedToolUse] = field(default_factory=dict)
    pending_text_start: dict[int, MessageStreamEvent] = field(default_factory=dict)
    emitted_blocks: list[BlockDescriptor] = field(default_factory=list)
    original_had_tool_use: bool = False
    judge_error_occurred: bool = False


class SimpleLLMPolicy(BasePolicy, AnthropicHookPolicy):
    """Policy that applies plain-English instructions to Anthropic LLM response blocks.

    Each content block is evaluated by a judge LLM which can pass it through
    or replace it with different content. Supports cross-type replacement
    (text <-> tool_use).

    Config:
        model: Judge LLM model identifier (default: "claude-haiku-4-5")
        instructions: Plain-English instructions for the judge (required)
        on_error: Action on judge failure - "pass" (default) allows content through
            with an injected warning, "block" rejects content entirely
        temperature: Sampling temperature for judge (default: 0.0)
        max_tokens: Max output tokens for judge (default: 4096)
    """

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name for the policy."""
        return "SimpleLLM"

    def __init__(self, config: SimpleLLMJudgeConfig | None = None):
        """Initialize with judge config."""
        parsed = self._init_config(config, SimpleLLMJudgeConfig)

        settings = get_settings()
        self._config = SimpleLLMJudgeConfig(
            model=settings.llm_judge_model or parsed.model,
            api_base=settings.llm_judge_api_base or parsed.api_base,
            instructions=parsed.instructions,
            temperature=parsed.temperature,
            max_tokens=parsed.max_tokens,
            on_error=parsed.on_error,
            max_retries=parsed.max_retries,
            retry_delay=parsed.retry_delay,
            auth_provider=parsed.auth_provider,
        )

        self._auth_provider: AuthProvider = parse_auth_provider(parsed.auth_provider)

        if self._config.on_error == "pass":
            logger.warning(
                "SimpleLLMPolicy on_error='pass': judge failures will allow "
                "content through with an injected warning notification. "
                "Use on_error='block' to reject content on judge failure."
            )

    # ========================================================================
    # Request-scoped state accessors
    # ========================================================================

    def _anthropic_state(self, context: "PolicyContext") -> _SimpleLLMAnthropicState:
        return context.get_request_state(self, _SimpleLLMAnthropicState, _SimpleLLMAnthropicState)

    # ========================================================================
    # Shared helpers
    # ========================================================================

    def _block_descriptor_from_text(self, text: str) -> BlockDescriptor:
        return BlockDescriptor(type="text", content=text)

    def _block_descriptor_from_tool(self, name: str, input_data: "JSONObject | str") -> BlockDescriptor:
        input_str = json.dumps(input_data) if not isinstance(input_data, str) else input_data
        return BlockDescriptor(type="tool_use", content=f"{name}({input_str})")

    def _block_descriptor_from_replacement(self, block: ReplacementBlock) -> BlockDescriptor:
        if block.type == "tool_use":
            input_str = json.dumps(block.input or {})
            return BlockDescriptor(type="tool_use", content=f"{block.name}({input_str})")
        return BlockDescriptor(type="text", content=block.text or "")

    def _replacement_to_anthropic_block(self, block: ReplacementBlock) -> "AnthropicContentBlock":
        if block.type == "tool_use":
            return {
                "type": "tool_use",
                "id": f"toolu_{uuid4().hex[:24]}",
                "name": block.name or "",
                "input": block.input or {},
            }
        return {"type": "text", "text": block.text or ""}

    async def _judge_block(
        self,
        descriptor: BlockDescriptor,
        emitted_blocks: list[BlockDescriptor],
        context: "PolicyContext",
    ) -> JudgeAction:
        """Call the judge LLM.

        Always returns a JudgeAction — on error, applies the on_error policy
        (returning "pass" or "block") so callers never handle None.
        """
        try:
            credential = await context.credential_manager.resolve(self._auth_provider, context)
            result = await call_simple_llm_judge(
                self._config,
                descriptor,
                tuple(emitted_blocks),
                credential=credential,
            )
            context.record_event(
                "policy.simple_llm.judge_result",
                {
                    "summary": f"Judge decided '{result.action}' for {descriptor.type} block",
                    "action": result.action,
                    "block_type": descriptor.type,
                },
            )
            return result
        except Exception as exc:
            logger.error(f"SimpleLLM judge failed: {exc}", exc_info=True)
            context.record_event(
                "policy.simple_llm.judge_error",
                {
                    "summary": f"Judge error for {descriptor.type} block: {exc}",
                    "error": str(exc),
                    "on_error": self._config.on_error,
                },
            )
            return JudgeAction(action=self._config.on_error, judge_failed=True)

    def _correct_anthropic_stop_reason(
        self, response: "AnthropicResponse", content: list["AnthropicContentBlock"]
    ) -> "AnthropicResponse":
        has_tool_use = any(b.get("type") == "tool_use" for b in content)
        expected = "tool_use" if has_tool_use else "end_turn"
        if response.get("stop_reason") != expected:
            response = cast("AnthropicResponse", dict(response))  # shallow copy preserves TypedDict shape
            response["stop_reason"] = expected
        return response

    # ========================================================================
    # Anthropic hooks (via AnthropicHookPolicy)
    # ========================================================================

    async def on_anthropic_response(
        self, response: "AnthropicResponse", context: "PolicyContext"
    ) -> "AnthropicResponse":
        """Judge each content block and apply replacements."""
        content = response.get("content", [])
        if not content:
            return response

        emitted_blocks: list[BlockDescriptor] = []
        new_content: list["AnthropicContentBlock"] = []
        judge_error_occurred = False

        for block in content:
            if not isinstance(block, dict):
                new_content.append(block)
                continue

            block_type = block.get("type")

            if block_type == "text":
                descriptor = self._block_descriptor_from_text(block.get("text", ""))
            elif block_type == "tool_use":
                descriptor = self._block_descriptor_from_tool(
                    block.get("name", ""),
                    block.get("input", {}),
                )
            else:
                new_content.append(block)
                continue

            action = await self._judge_block(descriptor, emitted_blocks, context)
            if action.judge_failed:
                judge_error_occurred = True

            if action.action == "pass":
                new_content.append(block)
                emitted_blocks.append(descriptor)
            elif action.action == "replace":
                for rblock in action.blocks or ():
                    emitted_blocks.append(self._block_descriptor_from_replacement(rblock))
                    new_content.append(self._replacement_to_anthropic_block(rblock))
            elif action.action == "block" and block_type == "tool_use":
                if action.judge_failed:
                    blocked_text = _blocked_tool_judge_failed_message(block.get("name", ""))
                else:
                    blocked_text = _blocked_tool_message(block.get("name", ""))
                emitted_blocks.append(self._block_descriptor_from_text(blocked_text))
                blocked_block: AnthropicTextBlock = {"type": "text", "text": blocked_text}
                new_content.append(blocked_block)

        if judge_error_occurred and self._config.on_error == "pass":
            warning_block: AnthropicTextBlock = {"type": "text", "text": JUDGE_UNAVAILABLE_WARNING}
            new_content.append(warning_block)
        elif judge_error_occurred and not new_content:
            error_block: AnthropicTextBlock = {"type": "text", "text": JUDGE_ERROR_BLOCKED_MESSAGE}
            new_content.append(error_block)

        modified_response = cast("AnthropicResponse", dict(response))
        modified_response["content"] = new_content
        return self._correct_anthropic_stop_reason(modified_response, new_content)

    # ========================================================================
    # Anthropic streaming
    # ========================================================================

    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        """Process streaming events, buffering content for judge evaluation."""
        if isinstance(event, RawContentBlockStartEvent):
            return self._handle_block_start(event, context)

        if isinstance(event, RawContentBlockDeltaEvent):
            return self._handle_block_delta(event, context)

        if isinstance(event, RawContentBlockStopEvent):
            return await self._handle_block_stop(event, context)

        if isinstance(event, RawMessageDeltaEvent):
            return self._handle_message_delta(event, context)

        return [event]

    def _handle_block_start(
        self, event: RawContentBlockStartEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        state = self._anthropic_state(context)
        index = event.index
        cb = event.content_block

        if isinstance(cb, ToolUseBlock):
            state.tool_buffer[index] = _BufferedToolUse(id=cb.id, name=cb.name)
            state.original_had_tool_use = True
            return []  # suppress start until judge decides

        if hasattr(cb, "type") and cb.type == "text":
            state.text_buffer[index] = ""
            # Buffer the start — don't emit yet. Claude sometimes sends a text
            # block start immediately followed by a stop with no deltas (empty
            # text block before tool_use). Emitting the start now then closing
            # it with no delta produces an empty text block in the client's
            # conversation history, which the Anthropic API rejects with 400
            # ("text content blocks must be non-empty"). We emit start+delta+stop
            # together in _handle_block_stop once we know the text is non-empty.
            state.pending_text_start[index] = cast(MessageStreamEvent, event)
            return []

        return [event]

    def _handle_block_delta(
        self, event: RawContentBlockDeltaEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        state = self._anthropic_state(context)
        index = event.index
        delta = event.delta

        if isinstance(delta, TextDelta) and index in state.text_buffer:
            state.text_buffer[index] += delta.text
            return []  # buffer

        if isinstance(delta, InputJSONDelta) and index in state.tool_buffer:
            state.tool_buffer[index].input_json += delta.partial_json
            return []  # buffer

        return [event]

    async def _handle_block_stop(
        self, event: RawContentBlockStopEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        state = self._anthropic_state(context)
        index = event.index

        # Text block stop
        if index in state.text_buffer:
            text = state.text_buffer.pop(index)
            pending_start = state.pending_text_start.pop(index, None)

            # Suppress entirely if the text is empty — emitting an empty text
            # block (start + stop with no content) produces {"type":"text","text":""}
            # in the client's conversation history, which Anthropic rejects with
            # 400 "text content blocks must be non-empty" on the next turn.
            if not text:
                return []

            descriptor = self._block_descriptor_from_text(text)
            action = await self._judge_block(descriptor, state.emitted_blocks, context)
            if action.judge_failed:
                state.judge_error_occurred = True

            if action.action == "pass":
                state.emitted_blocks.append(descriptor)
                events: list[MessageStreamEvent] = []
                if pending_start is not None:
                    events.append(pending_start)
                events.extend(self._emit_anthropic_text_events(index, text, event))
                return events
            elif action.action == "replace":
                return self._emit_anthropic_replacement_events(index, action, state, event)
            # Judge blocked the text block — suppress entirely (pending_start was
            # never emitted, so there's nothing to close).
            return []

        # Tool block stop
        if index in state.tool_buffer:
            buffered = state.tool_buffer.pop(index)
            try:
                input_data: "JSONObject | str" = (
                    json.loads(buffered.input_json) if buffered.input_json else {}
                )  # tool inputs are always objects in practice
            except json.JSONDecodeError:
                logger.warning(f"Malformed tool input JSON for '{buffered.name}', using raw string")
                input_data = {"_raw": buffered.input_json}
            descriptor = self._block_descriptor_from_tool(buffered.name, input_data)
            action = await self._judge_block(descriptor, state.emitted_blocks, context)
            if action.judge_failed:
                state.judge_error_occurred = True

            if action.action == "pass":
                state.emitted_blocks.append(descriptor)
                return self._emit_anthropic_tool_events(index, buffered, event)
            elif action.action == "replace":
                return self._emit_anthropic_replacement_events(index, action, state, event)
            # Tool start was suppressed — emit a text block so the client
            # knows the tool call was blocked and can continue the conversation.
            if action.judge_failed:
                blocked_text = _blocked_tool_judge_failed_message(buffered.name)
            else:
                blocked_text = _blocked_tool_message(buffered.name)
            state.emitted_blocks.append(self._block_descriptor_from_text(blocked_text))
            return self._make_anthropic_text_block_events(index, blocked_text)

        return [cast(MessageStreamEvent, event)]

    def _handle_message_delta(self, event: RawMessageDeltaEvent, context: "PolicyContext") -> list[MessageStreamEvent]:
        """Handle message_delta event: inject warning and correct stop_reason.

        The message_delta event carries stop_reason and usage, and comes after
        all content blocks but before message_stop. Warning text blocks must be
        inserted BEFORE this event to maintain valid Anthropic streaming order
        (content blocks after message_delta violate the protocol and can corrupt
        the client's conversation history).
        """
        state = self._anthropic_state(context)
        events: list[MessageStreamEvent] = []

        # Inject judge-unavailable warning as a content block before message_delta
        if state.judge_error_occurred and self._config.on_error == "pass":
            warning_index = len(state.emitted_blocks)
            events.extend(self._make_anthropic_warning_events(warning_index))
        elif state.judge_error_occurred and not state.emitted_blocks:
            events.extend(self._make_anthropic_text_block_events(0, JUDGE_ERROR_BLOCKED_MESSAGE))

        # Correct stop_reason if the emitted block types differ from the original
        has_emitted_tool = any(b.type == "tool_use" for b in state.emitted_blocks)
        expected_stop = "tool_use" if has_emitted_tool else "end_turn"
        if event.delta.stop_reason != expected_stop:
            event = RawMessageDeltaEvent.model_construct(
                type="message_delta",
                delta=event.delta.model_copy(update={"stop_reason": expected_stop}),
                usage=event.usage,
            )

        events.append(cast(MessageStreamEvent, event))
        return events

    def _emit_anthropic_text_events(
        self,
        index: int,
        text: str,
        stop_event: RawContentBlockStopEvent,
    ) -> list[MessageStreamEvent]:
        """Emit buffered text as a single delta + stop."""
        text_delta = TextDelta.model_construct(type="text_delta", text=text)
        delta_event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta", index=index, delta=text_delta
        )
        return [cast(MessageStreamEvent, delta_event), cast(MessageStreamEvent, stop_event)]

    def _emit_anthropic_tool_events(
        self,
        index: int,
        buffered: _BufferedToolUse,
        stop_event: RawContentBlockStopEvent,
    ) -> list[MessageStreamEvent]:
        """Reconstruct tool_use block events: start + json delta + stop."""
        tool_block = ToolUseBlock(type="tool_use", id=buffered.id, name=buffered.name, input={})
        start_event = RawContentBlockStartEvent(type="content_block_start", index=index, content_block=tool_block)
        json_delta = InputJSONDelta(type="input_json_delta", partial_json=buffered.input_json or "{}")
        delta_event = RawContentBlockDeltaEvent(type="content_block_delta", index=index, delta=json_delta)
        return [
            cast(MessageStreamEvent, start_event),
            cast(MessageStreamEvent, delta_event),
            cast(MessageStreamEvent, stop_event),
        ]

    def _make_anthropic_text_block_events(self, index: int, text: str) -> list[MessageStreamEvent]:
        """Emit a complete text block (start + delta + stop) with the given text."""
        text_block = TextBlock(type="text", text="")
        start = RawContentBlockStartEvent(type="content_block_start", index=index, content_block=text_block)
        text_delta = TextDelta.model_construct(type="text_delta", text=text)
        delta = RawContentBlockDeltaEvent.model_construct(type="content_block_delta", index=index, delta=text_delta)
        stop = RawContentBlockStopEvent(type="content_block_stop", index=index)
        return [
            cast(MessageStreamEvent, start),
            cast(MessageStreamEvent, delta),
            cast(MessageStreamEvent, stop),
        ]

    def _make_anthropic_warning_events(self, index: int) -> list[MessageStreamEvent]:
        """Emit a warning text block for judge-unavailable notification."""
        return self._make_anthropic_text_block_events(index, JUDGE_UNAVAILABLE_WARNING)

    def _emit_anthropic_replacement_events(
        self,
        index: int,
        action: JudgeAction,
        state: _SimpleLLMAnthropicState,
        stop_event: RawContentBlockStopEvent,
    ) -> list[MessageStreamEvent]:
        """Emit replacement block events."""
        events: list[MessageStreamEvent] = []

        for rblock in action.blocks or ():
            state.emitted_blocks.append(self._block_descriptor_from_replacement(rblock))

            if rblock.type == "text":
                text_block = TextBlock(type="text", text="")
                start = RawContentBlockStartEvent(type="content_block_start", index=index, content_block=text_block)
                text_delta = TextDelta.model_construct(type="text_delta", text=rblock.text or "")
                delta = RawContentBlockDeltaEvent.model_construct(
                    type="content_block_delta", index=index, delta=text_delta
                )
                events.extend(
                    [
                        cast(MessageStreamEvent, start),
                        cast(MessageStreamEvent, delta),
                    ]
                )

            elif rblock.type == "tool_use":
                tool_id = f"toolu_{uuid4().hex[:24]}"
                tool_block = ToolUseBlock(type="tool_use", id=tool_id, name=rblock.name or "", input={})
                start = RawContentBlockStartEvent(type="content_block_start", index=index, content_block=tool_block)
                json_str = json.dumps(rblock.input or {})
                json_delta = InputJSONDelta(type="input_json_delta", partial_json=json_str)
                delta = RawContentBlockDeltaEvent(type="content_block_delta", index=index, delta=json_delta)
                events.extend(
                    [
                        cast(MessageStreamEvent, start),
                        cast(MessageStreamEvent, delta),
                    ]
                )

        events.append(cast(MessageStreamEvent, stop_event))
        return events

    async def on_anthropic_streaming_policy_complete(self, context: "PolicyContext") -> None:
        """Clean up per-request Anthropic state."""
        context.pop_request_state(self, _SimpleLLMAnthropicState)


__all__ = ["SimpleLLMPolicy", "SimpleLLMJudgeConfig"]
