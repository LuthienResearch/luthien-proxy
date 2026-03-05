"""SimpleLLMPolicy - Apply plain-English instructions to LLM response blocks.

This policy evaluates each content block (text or tool_use) in an LLM response
against configurable instructions using a judge LLM. The judge can pass blocks
through or replace them with different content, including cross-type replacement
(e.g., replacing a tool_use with text).

Supports both OpenAI and Anthropic API formats, streaming and non-streaming.

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
from litellm.types.utils import (
    ChatCompletionMessageToolCall,
    Choices,
    Function,
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
    from luthien_proxy.llm.types.anthropic import (
        AnthropicRequest,
        AnthropicResponse,
    )
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext

logger = logging.getLogger(__name__)


@dataclass
class _BufferedToolUse:
    id: str
    name: str
    input_json: str = ""


@dataclass
class _SimpleLLMAnthropicState:
    text_buffer: dict[int, str] = field(default_factory=dict)
    tool_buffer: dict[int, _BufferedToolUse] = field(default_factory=dict)
    emitted_blocks: list[BlockDescriptor] = field(default_factory=list)
    original_had_tool_use: bool = False


@dataclass
class _SimpleLLMOpenAIState:
    emitted_blocks: list[BlockDescriptor] = field(default_factory=list)
    original_had_tool_use: bool = False


class SimpleLLMPolicy(BasePolicy, OpenAIPolicyInterface, AnthropicExecutionInterface):
    """Policy that applies plain-English instructions to LLM response blocks.

    Each content block is evaluated by a judge LLM which can pass it through
    or replace it with different content. Supports cross-type replacement
    (text <-> tool_use).

    Config:
        model: Judge LLM model identifier (default: "claude-haiku-4-5")
        instructions: Plain-English instructions for the judge (required)
        on_error: Error handling - "pass" allows content, "block" drops it
        temperature: Sampling temperature for judge (default: 0.0)
        max_tokens: Max output tokens for judge (default: 4096)
    """

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name for the policy."""
        return "SimpleLLM"

    def __init__(self, config: SimpleLLMJudgeConfig | dict[str, Any] | None = None):
        """Initialize with judge config. Resolves API key from settings."""
        parsed = self._init_config(config, SimpleLLMJudgeConfig)

        settings = get_settings()
        resolved_api_key = parsed.api_key or settings.llm_judge_api_key or settings.litellm_master_key or None

        self._config = SimpleLLMJudgeConfig(
            model=settings.llm_judge_model or parsed.model,
            api_base=settings.llm_judge_api_base or parsed.api_base,
            api_key=resolved_api_key,
            instructions=parsed.instructions,
            temperature=parsed.temperature,
            max_tokens=parsed.max_tokens,
            on_error=parsed.on_error,
        )

    # ========================================================================
    # Request-scoped state accessors
    # ========================================================================

    def _anthropic_state(self, context: "PolicyContext") -> _SimpleLLMAnthropicState:
        return context.get_request_state(self, _SimpleLLMAnthropicState, _SimpleLLMAnthropicState)

    def _openai_state(self, ctx: "StreamingPolicyContext") -> _SimpleLLMOpenAIState:
        return ctx.policy_ctx.get_request_state(self, _SimpleLLMOpenAIState, _SimpleLLMOpenAIState)

    # ========================================================================
    # Shared helpers
    # ========================================================================

    def _block_descriptor_from_text(self, text: str) -> BlockDescriptor:
        return BlockDescriptor(type="text", content=text)

    def _block_descriptor_from_tool(self, name: str, input_data: Any) -> BlockDescriptor:
        input_str = json.dumps(input_data) if not isinstance(input_data, str) else input_data
        return BlockDescriptor(type="tool_use", content=f"{name}({input_str})")

    def _block_descriptor_from_replacement(self, block: ReplacementBlock) -> BlockDescriptor:
        if block.type == "text":
            return BlockDescriptor(type="text", content=block.text or "")
        if block.type == "tool_use":
            input_str = json.dumps(block.input or {})
            return BlockDescriptor(type="tool_use", content=f"{block.name}({input_str})")
        return BlockDescriptor(type=block.type, content=block.text or "")

    def _replacement_to_anthropic_block(self, block: ReplacementBlock, _index: int) -> dict[str, Any]:
        if block.type == "text":
            return {"type": "text", "text": block.text or ""}
        if block.type == "tool_use":
            return {
                "type": "tool_use",
                "id": f"toolu_{uuid4().hex[:24]}",
                "name": block.name or "",
                "input": block.input or {},
            }
        return {"type": block.type, "text": block.text or ""}

    async def _judge_block(
        self,
        descriptor: BlockDescriptor,
        emitted_blocks: list[BlockDescriptor],
        context: "PolicyContext",
    ) -> JudgeAction | None:
        """Call the judge LLM. Returns None on error."""
        try:
            result = await call_simple_llm_judge(
                self._config,
                descriptor,
                tuple(emitted_blocks),
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
            return None

    def _apply_on_error(self) -> str:
        """Return 'pass' or 'block' based on on_error config."""
        return self._config.on_error

    def _correct_anthropic_stop_reason(self, response: dict[str, Any], content: list[dict[str, Any]]) -> dict[str, Any]:
        has_tool_use = any(b.get("type") == "tool_use" for b in content)
        expected = "tool_use" if has_tool_use else "end_turn"
        if response.get("stop_reason") != expected:
            response = dict(response)
            response["stop_reason"] = expected
        return response

    def _correct_openai_finish_reason(self, choice: Choices, has_tool_calls: bool) -> None:
        expected = "tool_calls" if has_tool_calls else "stop"
        if choice.finish_reason != expected:
            choice.finish_reason = expected

    # ========================================================================
    # OpenAI non-streaming
    # ========================================================================

    async def on_openai_request(self, request: "Request", context: "PolicyContext") -> "Request":
        """Pass through request unchanged."""
        return request

    async def on_openai_response(self, response: "ModelResponse", context: "PolicyContext") -> "ModelResponse":
        """Judge each content block and apply replacements."""
        if not response.choices:
            return response

        for choice in response.choices:
            if not isinstance(choice, Choices):
                continue

            emitted_blocks: list[BlockDescriptor] = []
            new_content: str | None = None
            new_tool_calls: list[ChatCompletionMessageToolCall] = []

            # Process text content
            if isinstance(choice.message.content, str) and choice.message.content:
                descriptor = self._block_descriptor_from_text(choice.message.content)
                action = await self._judge_block(descriptor, emitted_blocks, context)

                if action is None:
                    if self._apply_on_error() == "pass":
                        new_content = choice.message.content
                        emitted_blocks.append(descriptor)
                    # else block: drop it
                elif action.action == "pass":
                    new_content = choice.message.content
                    emitted_blocks.append(descriptor)
                else:
                    # replace
                    for rblock in action.blocks or ():
                        emitted_blocks.append(self._block_descriptor_from_replacement(rblock))
                        if rblock.type == "text":
                            if new_content is None:
                                new_content = rblock.text or ""
                            else:
                                new_content += rblock.text or ""
                        elif rblock.type == "tool_use":
                            new_tool_calls.append(
                                ChatCompletionMessageToolCall(
                                    id=f"call_{uuid4().hex[:24]}",
                                    function=Function(
                                        name=rblock.name or "",
                                        arguments=json.dumps(rblock.input or {}),
                                    ),
                                )
                            )

            # Process tool calls
            if choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    descriptor = self._block_descriptor_from_tool(
                        tc.function.name or "",
                        tc.function.arguments,
                    )
                    action = await self._judge_block(descriptor, emitted_blocks, context)

                    if action is None:
                        if self._apply_on_error() == "pass":
                            new_tool_calls.append(tc)
                            emitted_blocks.append(descriptor)
                        # else block: drop
                    elif action.action == "pass":
                        new_tool_calls.append(tc)
                        emitted_blocks.append(descriptor)
                    else:
                        for rblock in action.blocks or ():
                            emitted_blocks.append(self._block_descriptor_from_replacement(rblock))
                            if rblock.type == "text":
                                if new_content is None:
                                    new_content = rblock.text or ""
                                else:
                                    new_content += rblock.text or ""
                            elif rblock.type == "tool_use":
                                new_tool_calls.append(
                                    ChatCompletionMessageToolCall(
                                        id=f"call_{uuid4().hex[:24]}",
                                        function=Function(
                                            name=rblock.name or "",
                                            arguments=json.dumps(rblock.input or {}),
                                        ),
                                    )
                                )

            choice.message.content = new_content
            choice.message.tool_calls = new_tool_calls if new_tool_calls else None

            has_tool_calls = bool(new_tool_calls)
            self._correct_openai_finish_reason(choice, has_tool_calls)

        return response

    # ========================================================================
    # OpenAI streaming
    # ========================================================================

    async def on_chunk_received(self, ctx: "StreamingPolicyContext") -> None:
        """Suppress auto-forwarding; blocks are judged on completion."""
        pass

    async def on_content_delta(self, ctx: "StreamingPolicyContext") -> None:
        """Suppress; content is buffered by StreamState."""
        pass

    async def on_tool_call_delta(self, ctx: "StreamingPolicyContext") -> None:
        """Suppress; tool calls are buffered by StreamState."""
        pass

    async def on_finish_reason(self, ctx: "StreamingPolicyContext") -> None:
        """Suppress; handled by on_content_complete / on_stream_complete."""
        pass

    async def on_content_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Judge completed content block and emit result."""
        block = ctx.original_streaming_response_state.just_completed
        if not isinstance(block, ContentStreamBlock):
            return

        state = self._openai_state(ctx)
        descriptor = self._block_descriptor_from_text(block.content)
        action = await self._judge_block(descriptor, state.emitted_blocks, ctx.policy_ctx)

        if action is None:
            if self._apply_on_error() == "pass":
                await send_text(ctx, block.content)
                state.emitted_blocks.append(descriptor)
                await self._emit_openai_content_finish(ctx)
            # else block: drop
        elif action.action == "pass":
            await send_text(ctx, block.content)
            state.emitted_blocks.append(descriptor)
            await self._emit_openai_content_finish(ctx)
        else:
            await self._emit_openai_replacements(ctx, action, state)
            await self._emit_openai_content_finish(ctx)

    async def on_tool_call_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Judge completed tool call and emit result."""
        block = ctx.original_streaming_response_state.just_completed
        if not isinstance(block, ToolCallStreamBlock):
            return

        state = self._openai_state(ctx)
        state.original_had_tool_use = True
        descriptor = self._block_descriptor_from_tool(block.name, block.arguments)
        action = await self._judge_block(descriptor, state.emitted_blocks, ctx.policy_ctx)

        if action is None:
            if self._apply_on_error() == "pass":
                await send_tool_call(ctx, block.tool_call)
                state.emitted_blocks.append(descriptor)
            # else block: drop
        elif action.action == "pass":
            await send_tool_call(ctx, block.tool_call)
            state.emitted_blocks.append(descriptor)
        else:
            await self._emit_openai_replacements(ctx, action, state)

    async def on_stream_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Emit corrected finish_reason for tool call responses."""
        finish_reason = ctx.original_streaming_response_state.finish_reason
        if not finish_reason:
            return

        state = self._openai_state(ctx)

        # Correct finish_reason if block types changed
        has_emitted_tool = any(b.type == "tool_use" for b in state.emitted_blocks)
        corrected_reason = "tool_calls" if has_emitted_tool else "stop"

        # Only emit finish chunk for tool call responses
        # (content responses get finish in on_content_complete)
        if state.original_had_tool_use or has_emitted_tool:
            last_chunk = get_last_ingress_chunk(ctx)
            chunk_id = last_chunk.id if last_chunk else None
            model = last_chunk.model if last_chunk else "luthien-policy"
            finish_chunk = create_finish_chunk(
                finish_reason=corrected_reason,
                model=model,
                chunk_id=chunk_id,
            )
            await send_chunk(ctx, finish_chunk)

    async def on_streaming_policy_complete(self, ctx: "StreamingPolicyContext") -> None:
        """Clean up per-request OpenAI state."""
        ctx.policy_ctx.pop_request_state(self, _SimpleLLMOpenAIState)

    async def _emit_openai_content_finish(self, ctx: "StreamingPolicyContext") -> None:
        """Emit finish_reason chunk after content block."""
        last_chunk = get_last_ingress_chunk(ctx)
        if last_chunk and last_chunk.choices and last_chunk.choices[0].finish_reason:
            state = self._openai_state(ctx)
            has_tool = any(b.type == "tool_use" for b in state.emitted_blocks)
            reason = "tool_calls" if has_tool else last_chunk.choices[0].finish_reason
            finish_chunk = create_finish_chunk(
                finish_reason=reason,
                model=last_chunk.model,
                chunk_id=last_chunk.id,
            )
            await send_chunk(ctx, finish_chunk)

    async def _emit_openai_replacements(
        self,
        ctx: "StreamingPolicyContext",
        action: JudgeAction,
        state: _SimpleLLMOpenAIState,
    ) -> None:
        """Emit replacement blocks for OpenAI streaming."""
        for rblock in action.blocks or ():
            state.emitted_blocks.append(self._block_descriptor_from_replacement(rblock))
            if rblock.type == "text":
                text = rblock.text or ""
                if text:
                    await send_text(ctx, text)
            elif rblock.type == "tool_use":
                tc = ChatCompletionMessageToolCall(
                    id=f"call_{uuid4().hex[:24]}",
                    function=Function(
                        name=rblock.name or "",
                        arguments=json.dumps(rblock.input or {}),
                    ),
                )
                await send_tool_call(ctx, tc)

    # ========================================================================
    # Anthropic execution interface
    # ========================================================================

    def run_anthropic(
        self, io: AnthropicPolicyIOProtocol, context: "PolicyContext"
    ) -> AsyncIterator[AnthropicPolicyEmission]:
        """Run Anthropic request lifecycle with block-level judging."""

        async def _run() -> AsyncIterator[AnthropicPolicyEmission]:
            final_request = await self.on_anthropic_request(io.request, context)
            io.set_request(final_request)

            if final_request.get("stream", False):
                async for event in io.stream(final_request):
                    emitted_events = await self.on_anthropic_stream_event(event, context)
                    for emitted_event in emitted_events:
                        yield emitted_event
                return

            response = await io.complete(final_request)
            yield await self.on_anthropic_response(response, context)

        return _run()

    # ========================================================================
    # Anthropic non-streaming
    # ========================================================================

    async def on_anthropic_request(self, request: "AnthropicRequest", context: "PolicyContext") -> "AnthropicRequest":
        """Pass through request unchanged."""
        return request

    async def on_anthropic_response(
        self, response: "AnthropicResponse", context: "PolicyContext"
    ) -> "AnthropicResponse":
        """Judge each content block and apply replacements."""
        content = response.get("content", [])
        if not content:
            return response

        emitted_blocks: list[BlockDescriptor] = []
        new_content: list[Any] = []

        for block in content:
            if not isinstance(block, dict):
                new_content.append(block)
                continue

            block_type = block.get("type")

            if block_type == "text":
                descriptor = self._block_descriptor_from_text(block.get("text", ""))
                action = await self._judge_block(descriptor, emitted_blocks, context)

                if action is None:
                    if self._apply_on_error() == "pass":
                        new_content.append(block)
                        emitted_blocks.append(descriptor)
                elif action.action == "pass":
                    new_content.append(block)
                    emitted_blocks.append(descriptor)
                else:
                    for i, rblock in enumerate(action.blocks or ()):
                        emitted_blocks.append(self._block_descriptor_from_replacement(rblock))
                        new_content.append(self._replacement_to_anthropic_block(rblock, i))

            elif block_type == "tool_use":
                descriptor = self._block_descriptor_from_tool(
                    block.get("name", ""),
                    block.get("input", {}),
                )
                action = await self._judge_block(descriptor, emitted_blocks, context)

                if action is None:
                    if self._apply_on_error() == "pass":
                        new_content.append(block)
                        emitted_blocks.append(descriptor)
                elif action.action == "pass":
                    new_content.append(block)
                    emitted_blocks.append(descriptor)
                else:
                    for i, rblock in enumerate(action.blocks or ()):
                        emitted_blocks.append(self._block_descriptor_from_replacement(rblock))
                        new_content.append(self._replacement_to_anthropic_block(rblock, i))
            else:
                new_content.append(block)

        modified_response = dict(response)
        modified_response["content"] = new_content
        return cast("AnthropicResponse", self._correct_anthropic_stop_reason(modified_response, new_content))

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
            return [event]  # pass through text start

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
            descriptor = self._block_descriptor_from_text(text)
            action = await self._judge_block(descriptor, state.emitted_blocks, context)

            if action is None:
                if self._apply_on_error() == "pass":
                    state.emitted_blocks.append(descriptor)
                    return self._emit_anthropic_text_events(index, text, event)
                return [cast(MessageStreamEvent, event)]
            elif action.action == "pass":
                state.emitted_blocks.append(descriptor)
                return self._emit_anthropic_text_events(index, text, event)
            else:
                return self._emit_anthropic_replacement_events(index, action, state, event)

        # Tool block stop
        if index in state.tool_buffer:
            buffered = state.tool_buffer.pop(index)
            input_data = json.loads(buffered.input_json) if buffered.input_json else {}
            descriptor = self._block_descriptor_from_tool(buffered.name, input_data)
            action = await self._judge_block(descriptor, state.emitted_blocks, context)

            if action is None:
                if self._apply_on_error() == "pass":
                    state.emitted_blocks.append(descriptor)
                    return self._emit_anthropic_tool_events(index, buffered, event)
                return [cast(MessageStreamEvent, event)]
            elif action.action == "pass":
                state.emitted_blocks.append(descriptor)
                return self._emit_anthropic_tool_events(index, buffered, event)
            else:
                return self._emit_anthropic_replacement_events(index, action, state, event)

        return [cast(MessageStreamEvent, event)]

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

    async def on_anthropic_stream_complete(self, context: "PolicyContext") -> None:
        """No-op hook for parity with OpenAI lifecycle."""
        pass

    async def on_anthropic_streaming_policy_complete(self, context: "PolicyContext") -> None:
        """Clean up per-request Anthropic state."""
        context.pop_request_state(self, _SimpleLLMAnthropicState)


__all__ = ["SimpleLLMPolicy", "SimpleLLMJudgeConfig"]
