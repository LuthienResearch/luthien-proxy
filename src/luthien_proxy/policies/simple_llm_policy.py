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
    CatalogBadge,
    Category,
    UIMetadata,
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


def _blocked_tools_message(names: list[str]) -> str:
    """Default marker text listing every tool blocked or truncated in this response.

    Plural / singular variants share the same shape so downstream parsers can
    match either. The list is the order in which the upstream model emitted the
    blocks (which is what the user expects to see).
    """
    quoted = ", ".join(f"`{n}`" for n in names)
    if len(names) == 1:
        return f"[Tool call {quoted} was blocked by policy]"
    return f"[Tool calls {quoted} were blocked by policy]"


def _blocked_tools_judge_failed_message(names: list[str]) -> str:
    quoted = ", ".join(f"`{n}`" for n in names)
    if len(names) == 1:
        return f"[Tool call {quoted} blocked: policy evaluation unavailable]"
    return f"[Tool calls {quoted} blocked: policy evaluation unavailable]"


@dataclass
class _SimpleLLMAnthropicState:
    text_buffer: dict[int, str] = field(default_factory=dict)
    tool_buffer: dict[int, _BufferedToolUse] = field(default_factory=dict)
    pending_text_start: dict[int, MessageStreamEvent] = field(default_factory=dict)
    emitted_blocks: list[BlockDescriptor] = field(default_factory=list)
    original_had_tool_use: bool = False
    judge_error_occurred: bool = False
    # True once the judge-unavailable warning has been injected into the
    # stream. Used to avoid double-emission at message_delta time.
    warning_emitted: bool = False
    # True once any tool_use block has been emitted downstream. The Anthropic
    # API rejects an assistant message containing any non-tool_use content
    # after the first tool_use ("tool_use ids were found without tool_result
    # blocks immediately after" — see #708). Once this flag is set, every
    # downstream emission site in this policy must refuse to emit non-tool
    # content. The invariant is enforced inline at each emission point in
    # _handle_block_stop, _handle_message_delta, and
    # _emit_anthropic_replacement_events — there is no separate post-pass.
    tool_use_emitted: bool = False
    # True once a tool_use has been blocked by the judge. Subsequent
    # upstream tool_use blocks are dropped without judging: blocking one
    # tool means we've decided to intervene, and there's no clean way to
    # communicate "tool N was blocked" once tool N+1 has been emitted (the
    # marker text would land after a tool_use — see [[tool_use_emitted]]).
    # Truncating on first block keeps intent obvious to the model on the
    # next turn and avoids surfacing partial intervention.
    tool_blocking_engaged: bool = False
    # Names of tools dropped from this response — either blocked by the
    # judge or truncated as fallout from blocking an earlier tool. We defer
    # the user-facing marker until message_delta so a single text block
    # lists every blocked tool, rather than emitting one marker per block
    # (which would be impossible anyway: after the first marker emits, the
    # second can't follow without violating #708 in the marker-then-marker
    # case, and a single marker is what users want to see regardless).
    blocked_tool_names: list[str] = field(default_factory=list)
    # True if any of the drops in [[blocked_tool_names]] was due to the
    # judge erroring rather than explicitly blocking. Affects the marker
    # phrasing ("policy evaluation unavailable" vs. "blocked by policy").
    any_blocked_due_to_judge_failure: bool = False
    # Cumulative downstream offset from multi-block replacements: when one
    # upstream block is replaced with N>1 blocks, every subsequent downstream
    # index shifts by N-1 to avoid colliding with the extra emitted blocks.
    #
    # Invariant: every emitted content_block_{start,delta,stop}.index equals
    # `upstream_event.index + state.index_shift` at emission time. All emit
    # paths — buffered text, buffered tool_use, replacement events, AND
    # passthrough for unbuffered block types (e.g. thinking blocks) — must
    # apply this shift. Violating the invariant produces duplicate indices
    # downstream and breaks Anthropic SDK clients.
    index_shift: int = 0


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

    ui = UIMetadata(
        display_name="LLM-as-Judge",
        short_description="Apply plain-English instructions to evaluate and rewrite responses.",
        category=Category.ACTIVE_MONITORING,
        catalog_badges=(CatalogBadge.JUDGE,),
    )

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name for the policy."""
        return "SimpleLLM"

    def __init__(self, config: SimpleLLMJudgeConfig | None = None):
        """Initialize with judge config.

        Note: config=None will fail at runtime with ValidationError since auth_provider is required.
        Pass an explicit SimpleLLMJudgeConfig with auth_provider set.
        """
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
        tool_use_emitted = False
        tool_blocking_engaged = False
        blocked_tool_names: list[str] = []
        any_blocked_due_to_judge_failure = False

        for block in content:
            if not isinstance(block, dict):
                new_content.append(block)
                continue

            block_type = block.get("type")

            # Drop any tool_use after we've decided to block one in this
            # response (see [[tool_blocking_engaged]] in the streaming state).
            # Record the name so the consolidated marker lists it.
            if block_type == "tool_use" and tool_blocking_engaged:
                name = block.get("name", "") or ""
                blocked_tool_names.append(name)
                logger.warning(
                    "SimpleLLMPolicy: dropping tool_use '%s' after a prior tool was blocked",
                    name,
                )
                continue

            # Once a tool_use has been emitted, no non-tool content may
            # follow in this assistant message (#708).
            if block_type == "text" and tool_use_emitted:
                logger.warning("SimpleLLMPolicy: dropping text block following a prior tool_use (#708)")
                continue

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
                if block_type == "tool_use":
                    tool_use_emitted = True
            elif action.action == "replace":
                for rblock in action.blocks or ():
                    if rblock.type == "text" and tool_use_emitted:
                        logger.warning("SimpleLLMPolicy: dropping text replacement following a prior tool_use (#708)")
                        continue
                    emitted_blocks.append(self._block_descriptor_from_replacement(rblock))
                    new_content.append(self._replacement_to_anthropic_block(rblock))
                    if rblock.type == "tool_use":
                        tool_use_emitted = True
            elif action.action == "block" and block_type == "tool_use":
                # Engage truncation; record the name. The consolidated
                # marker is emitted after the loop. Skipping in-loop emission
                # also avoids the #708 violation when a prior tool_use is
                # already in new_content.
                tool_blocking_engaged = True
                blocked_tool_names.append(block.get("name", "") or "")
                if action.judge_failed:
                    any_blocked_due_to_judge_failure = True

        if judge_error_occurred and self._config.on_error == "pass":
            warning_block: AnthropicTextBlock = {"type": "text", "text": JUDGE_UNAVAILABLE_WARNING}
            # Place the warning before the first tool_use (if any) so it sits
            # in the pre-tool region — Anthropic rejects non-tool_use content
            # following a tool_use (#708).
            first_tool_idx = next(
                (i for i, b in enumerate(new_content) if b.get("type") == "tool_use"),
                None,
            )
            if first_tool_idx is None:
                new_content.append(warning_block)
            else:
                new_content.insert(first_tool_idx, warning_block)
        elif judge_error_occurred and not new_content and not blocked_tool_names:
            error_block: AnthropicTextBlock = {"type": "text", "text": JUDGE_ERROR_BLOCKED_MESSAGE}
            new_content.append(error_block)

        # Consolidated blocked-tools marker, listing every tool blocked or
        # truncated. Skip when a tool_use was emitted upstream (#708: text
        # can't follow a tool_use).
        if blocked_tool_names and not tool_use_emitted:
            if any_blocked_due_to_judge_failure:
                marker_text = _blocked_tools_judge_failed_message(blocked_tool_names)
            else:
                marker_text = _blocked_tools_message(blocked_tool_names)
            marker_block: AnthropicTextBlock = {"type": "text", "text": marker_text}
            new_content.append(marker_block)
        elif blocked_tool_names and tool_use_emitted:
            logger.warning(
                "SimpleLLMPolicy: %d blocked tool(s) could not be communicated "
                "(a tool_use was emitted upstream — #708): %r",
                len(blocked_tool_names),
                blocked_tool_names,
            )

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

        # Passthrough for unbuffered block types (thinking, redacted_thinking,
        # future types). Must apply index_shift to maintain monotonic indices
        # after a prior multi-block replacement.
        shifted = RawContentBlockStartEvent(
            type="content_block_start",
            index=index + state.index_shift,
            content_block=event.content_block,
        )
        return [cast(MessageStreamEvent, shifted)]

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

        # Passthrough delta for unbuffered block types (e.g. thinking deltas).
        # Apply index_shift to match the shifted start/stop emitted for the
        # same upstream block.
        shifted = RawContentBlockDeltaEvent(
            type="content_block_delta",
            index=index + state.index_shift,
            delta=event.delta,
        )
        return [cast(MessageStreamEvent, shifted)]

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

            # Once a tool_use is downstream, no text may follow — Anthropic
            # 400s next turn (#708). Drop silently; pending_start was buffered
            # and never emitted so the wire stays clean.
            if state.tool_use_emitted:
                logger.warning(
                    "SimpleLLMPolicy: dropping text block following a prior tool_use (#708)",
                )
                return []

            emitted_index = index + state.index_shift
            if action.action == "pass":
                state.emitted_blocks.append(descriptor)
                events: list[MessageStreamEvent] = []
                if pending_start is not None:
                    orig_start = cast(RawContentBlockStartEvent, pending_start)
                    shifted_start = RawContentBlockStartEvent(
                        type="content_block_start",
                        index=emitted_index,
                        content_block=orig_start.content_block,
                    )
                    events.append(cast(MessageStreamEvent, shifted_start))
                events.extend(self._emit_anthropic_text_events(emitted_index, text))
                return events
            elif action.action == "replace":
                return self._emit_anthropic_replacement_events(emitted_index, action, state)
            # Judge blocked the text block — suppress entirely (pending_start was
            # never emitted, so there's nothing to close).
            return []

        # Tool block stop
        if index in state.tool_buffer:
            buffered = state.tool_buffer.pop(index)

            # Once we've blocked any tool in this response, drop the rest of
            # the model's tool calls without judging. See
            # [[tool_blocking_engaged]]: partial intervention has no clean
            # way to communicate. Record the name so the deferred marker at
            # message_delta lists every dropped tool.
            if state.tool_blocking_engaged:
                state.blocked_tool_names.append(buffered.name)
                logger.warning(
                    "SimpleLLMPolicy: dropping tool_use '%s' after a prior tool was blocked",
                    buffered.name,
                )
                return []

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

            emitted_index = index + state.index_shift
            if action.action == "pass":
                # Inject the judge-unavailable warning BEFORE the first
                # tool_use if a judge failure has occurred. After that point
                # the tool_use-trailing invariant (#708) forbids inserting
                # text between or after tool_uses, so the warning becomes
                # best-effort: if the first judge error surfaces only after a
                # tool_use was already emitted, the warning is dropped
                # silently in _handle_message_delta.
                events: list[MessageStreamEvent] = []
                if (
                    state.judge_error_occurred
                    and self._config.on_error == "pass"
                    and not state.warning_emitted
                    and not state.tool_use_emitted
                ):
                    warning_descriptor = self._block_descriptor_from_text(JUDGE_UNAVAILABLE_WARNING)
                    state.emitted_blocks.append(warning_descriptor)
                    events.extend(self._make_anthropic_warning_events(emitted_index))
                    state.warning_emitted = True
                    state.index_shift += 1
                    emitted_index += 1
                state.emitted_blocks.append(descriptor)
                events.extend(self._emit_anthropic_tool_events(emitted_index, buffered))
                state.tool_use_emitted = True
                return events
            elif action.action == "replace":
                return self._emit_anthropic_replacement_events(emitted_index, action, state)
            # action == "block" — engage truncation, record the name, and
            # defer the marker emission to message_delta so a single text
            # block lists every blocked / truncated tool. Emitting here
            # would also fail the invariant when a prior tool_use is
            # already downstream (#708); deferral handles that uniformly.
            state.tool_blocking_engaged = True
            state.blocked_tool_names.append(buffered.name)
            if action.judge_failed:
                state.any_blocked_due_to_judge_failure = True
            return []

        # Passthrough stop for unbuffered block types (e.g. thinking blocks).
        # Apply index_shift to match the shifted start/delta emitted for the
        # same upstream block.
        shifted = RawContentBlockStopEvent(
            type="content_block_stop",
            index=index + state.index_shift,
        )
        return [cast(MessageStreamEvent, shifted)]

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

        # Inject judge-unavailable warning before message_delta only if no
        # tool_use is downstream (#708: any text after the first tool_use
        # 400s the next turn). When a tool_use is present, the warning was
        # either injected before it in _handle_block_stop or is dropped
        # here because the judge error surfaced too late to place safely.
        if (
            state.judge_error_occurred
            and self._config.on_error == "pass"
            and not state.warning_emitted
            and not state.tool_use_emitted
        ):
            warning_index = len(state.emitted_blocks)
            events.extend(self._make_anthropic_warning_events(warning_index))
            state.warning_emitted = True
        elif (
            state.judge_error_occurred
            and self._config.on_error == "pass"
            and not state.warning_emitted
            and state.tool_use_emitted
        ):
            logger.warning(
                "SimpleLLMPolicy: judge-unavailable warning could not be emitted "
                "(first judge failure occurred after tool_use was already streamed — #708)",
            )
        elif state.judge_error_occurred and not state.emitted_blocks and not state.blocked_tool_names:
            events.extend(self._make_anthropic_text_block_events(0, JUDGE_ERROR_BLOCKED_MESSAGE))

        # Emit the consolidated blocked-tools marker, listing every tool that
        # was blocked by the judge or truncated as fallout. Deferred until
        # message_delta so one text block covers them all. If a tool_use was
        # already emitted upstream, the marker can't follow it (#708) — log
        # and drop, conversation continuity wins.
        if state.blocked_tool_names and not state.tool_use_emitted:
            if state.any_blocked_due_to_judge_failure:
                marker_text = _blocked_tools_judge_failed_message(state.blocked_tool_names)
            else:
                marker_text = _blocked_tools_message(state.blocked_tool_names)
            marker_index = len(state.emitted_blocks)
            state.emitted_blocks.append(self._block_descriptor_from_text(marker_text))
            events.extend(self._make_anthropic_text_block_events(marker_index, marker_text))
        elif state.blocked_tool_names and state.tool_use_emitted:
            logger.warning(
                "SimpleLLMPolicy: %d blocked tool(s) could not be communicated to the client "
                "(a tool_use was already emitted — #708): %r",
                len(state.blocked_tool_names),
                state.blocked_tool_names,
            )

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
    ) -> list[MessageStreamEvent]:
        """Emit buffered text as a single delta + stop at the given (possibly shifted) index."""
        text_delta = TextDelta.model_construct(type="text_delta", text=text)
        delta_event = RawContentBlockDeltaEvent.model_construct(
            type="content_block_delta", index=index, delta=text_delta
        )
        stop_event = RawContentBlockStopEvent(type="content_block_stop", index=index)
        return [cast(MessageStreamEvent, delta_event), cast(MessageStreamEvent, stop_event)]

    def _emit_anthropic_tool_events(
        self,
        index: int,
        buffered: _BufferedToolUse,
    ) -> list[MessageStreamEvent]:
        """Reconstruct tool_use block events at the given (possibly shifted) index: start + json delta + stop."""
        tool_block = ToolUseBlock(type="tool_use", id=buffered.id, name=buffered.name, input={})
        start_event = RawContentBlockStartEvent(type="content_block_start", index=index, content_block=tool_block)
        json_delta = InputJSONDelta(type="input_json_delta", partial_json=buffered.input_json or "{}")
        delta_event = RawContentBlockDeltaEvent(type="content_block_delta", index=index, delta=json_delta)
        stop_event = RawContentBlockStopEvent(type="content_block_stop", index=index)
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
    ) -> list[MessageStreamEvent]:
        """Emit replacement block events with monotonically increasing indices.

        When the judge replaces one upstream block with N blocks, each emitted
        block gets a distinct index starting at `index` (which already includes
        any prior index_shift). For N>1 we update state.index_shift so that
        subsequent passthrough/replacement blocks land at non-colliding indices.
        """
        events: list[MessageStreamEvent] = []
        current_index = index

        for rblock in action.blocks or ():
            block_stop = RawContentBlockStopEvent(type="content_block_stop", index=current_index)

            if rblock.type == "text":
                # Once a tool_use is downstream, no text may follow (#708).
                # Drop the text replacement; don't consume an index slot.
                if state.tool_use_emitted:
                    logger.warning(
                        "SimpleLLMPolicy: dropping text replacement following a prior tool_use (#708)",
                    )
                    continue
                text_block = TextBlock(type="text", text="")
                start = RawContentBlockStartEvent(
                    type="content_block_start", index=current_index, content_block=text_block
                )
                text_delta = TextDelta.model_construct(type="text_delta", text=rblock.text or "")
                delta = RawContentBlockDeltaEvent.model_construct(
                    type="content_block_delta", index=current_index, delta=text_delta
                )
                events.extend(
                    [
                        cast(MessageStreamEvent, start),
                        cast(MessageStreamEvent, delta),
                        cast(MessageStreamEvent, block_stop),
                    ]
                )

            elif rblock.type == "tool_use":
                tool_id = f"toolu_{uuid4().hex[:24]}"
                tool_block = ToolUseBlock(type="tool_use", id=tool_id, name=rblock.name or "", input={})
                start = RawContentBlockStartEvent(
                    type="content_block_start", index=current_index, content_block=tool_block
                )
                json_str = json.dumps(rblock.input or {})
                json_delta = InputJSONDelta(type="input_json_delta", partial_json=json_str)
                delta = RawContentBlockDeltaEvent(type="content_block_delta", index=current_index, delta=json_delta)
                events.extend(
                    [
                        cast(MessageStreamEvent, start),
                        cast(MessageStreamEvent, delta),
                        cast(MessageStreamEvent, block_stop),
                    ]
                )

            else:
                # Unknown rblock type: skip, don't consume an index slot or
                # append a descriptor — keeps state.emitted_blocks aligned with
                # what was actually emitted.
                logger.warning(
                    "SimpleLLMPolicy: unknown replacement block type %r — skipping, index not consumed",
                    rblock.type,
                )
                continue

            state.emitted_blocks.append(self._block_descriptor_from_replacement(rblock))
            current_index += 1

        num_emitted = current_index - index
        # 1-for-1 replacement consumes the same upstream index slot it came
        # from, so downstream indices are unaffected. Only shift when N>1.
        if num_emitted > 1:
            state.index_shift += num_emitted - 1

        return events

    async def on_anthropic_streaming_policy_complete(self, context: "PolicyContext") -> None:
        """Clean up per-request Anthropic state."""
        context.pop_request_state(self, _SimpleLLMAnthropicState)


__all__ = ["SimpleLLMPolicy", "SimpleLLMJudgeConfig"]
