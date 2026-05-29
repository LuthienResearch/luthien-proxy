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
from typing import TYPE_CHECKING
from uuid import uuid4

from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    TextDelta,
    ToolUseBlock,
)

from luthien_proxy.credentials import (
    InferenceProviderRef,
    parse_auth_provider,
    parse_inference_provider,
)
from luthien_proxy.inference.dispatch import resolve_inference_provider
from luthien_proxy.policies.simple_llm_utils import (
    BlockDescriptor,
    JudgeAction,
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
from luthien_proxy.policy_core.anthropic_message_builder import (
    AnthropicMessageBuilder,
    BufferedTool,
)
from luthien_proxy.policy_core.judge_orchestrator import Bailed, JudgeOrchestrator
from luthien_proxy.settings import get_settings

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import AnthropicResponse
    from luthien_proxy.policy_core.policy_context import PolicyContext

logger = logging.getLogger(__name__)


JUDGE_UNAVAILABLE_WARNING = "⚠️ Safety judge unavailable — this response was not evaluated by the safety policy."

JUDGE_ERROR_BLOCKED_MESSAGE = (
    "❌ Response blocked: the safety judge encountered an error and the policy requires blocking on failure."
)


@dataclass(frozen=True)
class _PendingTool:
    """Tag attached to a concurrently-dispatched tool judge."""

    tool: BufferedTool


def _bail_on_block(action: JudgeAction) -> bool:
    """Bail predicate: a `block` decision cancels every later tool judge."""
    return action.action == "block"


def _parse_provider_ref(
    inference_provider: str | dict | None,
    auth_provider: str | dict | None,
) -> InferenceProviderRef:
    """Parse inference-provider reference, accepting the legacy `auth_provider` key.

    Requires exactly one of the two fields to be set. Both None defaults to
    `user_credentials` for back-compat with the pre-PR-#609 behavior that
    PR #603 made mandatory.
    """
    if inference_provider is not None and auth_provider is not None:
        raise ValueError(
            "Policy config has both 'inference_provider' and 'auth_provider'; "
            "use only 'inference_provider' (the old name is deprecated)."
        )
    if inference_provider is not None:
        return parse_inference_provider(inference_provider)
    if auth_provider is not None:
        return parse_auth_provider(auth_provider)
    return parse_inference_provider(None)


@dataclass
class _SimpleLLMAnthropicState:
    """Per-request state.

    The builder owns Anthropic-streaming concerns (upstream buffering, wire
    ordering, indices, invariants). The orchestrator owns concurrent tool
    judging and the early-bail rule. The only policy-specific scalar is
    `judge_error_occurred`, consulted at message_delta to decide whether
    to ask the builder for a warning or fallback message.

    Tool judges run concurrently — every tool's `block_stop` dispatches
    its judge as a task and returns immediately. Results are collected in
    submission order at `message_delta`. The first `block` cancels every
    pending tool judge; subsequent tools surface as `Bailed` and are
    recorded as blocked (their judges never ran).

    Text judges stay serial (await inline at text `block_stop`) so text
    can commit to the wire incrementally — deferring the text judge would
    cost the streaming property of the entire pre-tool region.
    """

    builder: AnthropicMessageBuilder = field(default_factory=AnthropicMessageBuilder)
    tool_judge: JudgeOrchestrator[_PendingTool, JudgeAction] = field(
        default_factory=lambda: JudgeOrchestrator(bail_predicate=_bail_on_block)
    )
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

        Note: config=None fails with ValidationError because `instructions` is
        required. The inference target defaults to `user_credentials` when
        neither `inference_provider` nor `auth_provider` is set.
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
            inference_provider=parsed.inference_provider,
            auth_provider=parsed.auth_provider,
        )

        self._inference_provider_ref: InferenceProviderRef = _parse_provider_ref(
            inference_provider=parsed.inference_provider,
            auth_provider=parsed.auth_provider,
        )

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

    async def _judge_block(
        self,
        descriptor: BlockDescriptor,
        previous_blocks: tuple[BlockDescriptor, ...],
        context: "PolicyContext",
    ) -> JudgeAction:
        """Call the judge LLM.

        Always returns a JudgeAction — on error, applies the on_error policy
        (returning "pass" or "block") so callers never handle None.
        """
        try:
            dispatch = await resolve_inference_provider(
                self._inference_provider_ref,
                context,
                context.inference_provider_registry,
                passthrough_default_model=self._config.model,
                passthrough_api_base=self._config.api_base,
                passthrough_name="simple_llm_judge_passthrough",
            )
            result = await call_simple_llm_judge(
                self._config,
                descriptor,
                previous_blocks,
                provider=dispatch.provider,
                credential_override=dispatch.credential_override,
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

    def _apply_replacement_to_builder(
        self,
        builder: AnthropicMessageBuilder,
        action: JudgeAction,
    ) -> list[MessageStreamEvent]:
        """Translate a judge `replace` action into builder commits.

        Text replacements emit immediately (or queue post-tool-buffer);
        tool_use replacements buffer for the tool flush. The builder
        guarantees the wire ordering invariant regardless of the order in
        which replacement blocks are emitted. Returns the streaming events
        emitted by `commit_text`; non-streaming callers ignore the result.
        """
        events: list[MessageStreamEvent] = []
        for rblock in action.blocks or ():
            if rblock.type == "text":
                events.extend(builder.commit_text(rblock.text or ""))
            elif rblock.type == "tool_use":
                tool_id = f"toolu_{uuid4().hex[:24]}"
                input_json = json.dumps(rblock.input or {})
                builder.buffer_tool(id=tool_id, name=rblock.name or "", input_json=input_json)
            else:
                logger.warning(
                    "SimpleLLMPolicy: unknown replacement block type %r — skipping",
                    rblock.type,
                )
        return events

    # ========================================================================
    # Anthropic non-streaming
    # ========================================================================

    async def on_anthropic_response(
        self, response: "AnthropicResponse", context: "PolicyContext"
    ) -> "AnthropicResponse":
        """Judge each content block via the shared builder and compose a wire-correct response.

        Routes per-block decisions through the same primitives the streaming
        path uses (`commit_text`, `buffer_tool`, `record_blocked_tool`,
        `note_judge_unavailable`, `set_fallback_text`) and then asks the
        builder for the finalized response. The trailing-tool_use invariant
        (#708) is enforced by the builder, not by per-policy reconstruction.
        """
        content = response.get("content", [])
        if not content:
            return response

        builder = AnthropicMessageBuilder()
        judge_error_occurred = False

        for block in content:
            if not isinstance(block, dict):
                builder.commit_raw_block(block)
                continue

            block_type = block.get("type")
            if block_type == "text":
                descriptor = BlockDescriptor(type="text", content=block.get("text", ""))
            elif block_type == "tool_use":
                descriptor = BlockDescriptor(
                    type="tool_use",
                    content=f"{block.get('name', '')}({json.dumps(block.get('input', {}))})",
                )
            else:
                builder.commit_raw_block(block)
                continue

            action = await self._judge_block(descriptor, builder.committed_descriptors, context)
            if action.judge_failed:
                judge_error_occurred = True

            if action.action == "pass":
                if block_type == "tool_use":
                    builder.buffer_tool(
                        id=str(block.get("id", "")),
                        name=str(block.get("name", "")),
                        input_json=json.dumps(block.get("input", {})),
                    )
                else:
                    builder.commit_text(block.get("text", ""))
            elif action.action == "replace":
                self._apply_replacement_to_builder(builder, action)
            elif action.action == "block" and block_type == "tool_use":
                builder.record_blocked_tool(
                    str(block.get("name", "") or ""),
                    judge_failed=action.judge_failed,
                )

        if judge_error_occurred:
            if self._config.on_error == "pass":
                builder.note_judge_unavailable(JUDGE_UNAVAILABLE_WARNING)
            else:
                builder.set_fallback_text(JUDGE_ERROR_BLOCKED_MESSAGE)

        return builder.to_anthropic_response(response)

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
            return await self._handle_message_delta(event, context)

        return [event]

    def _handle_block_start(
        self, event: RawContentBlockStartEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        builder = self._anthropic_state(context).builder
        cb = event.content_block

        if isinstance(cb, ToolUseBlock):
            builder.begin_tool_buffer(event.index, id=cb.id, name=cb.name)
            return []

        if hasattr(cb, "type") and cb.type == "text":
            builder.begin_text_buffer(event.index)
            return []

        # Passthrough (thinking, redacted_thinking, future block types).
        return builder.passthrough_start(event)

    def _handle_block_delta(
        self, event: RawContentBlockDeltaEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        builder = self._anthropic_state(context).builder
        delta = event.delta

        if isinstance(delta, TextDelta) and builder.append_text_delta(event.index, delta.text):
            return []
        if isinstance(delta, InputJSONDelta) and builder.append_tool_delta(event.index, delta.partial_json):
            return []

        return builder.passthrough_delta(event)

    async def _handle_block_stop(
        self, event: RawContentBlockStopEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        state = self._anthropic_state(context)
        builder = state.builder

        text = builder.take_text(event.index)
        if text is not None:
            return await self._handle_text_stop(state, text, context)

        tool = builder.take_tool(event.index)
        if tool is not None:
            return await self._handle_tool_stop(state, tool, context)

        return builder.passthrough_stop(event)

    async def _handle_text_stop(
        self,
        state: _SimpleLLMAnthropicState,
        text: str,
        context: "PolicyContext",
    ) -> list[MessageStreamEvent]:
        if not text:
            # Empty text block — Anthropic rejects on next turn. Suppress.
            return []

        descriptor = BlockDescriptor(type="text", content=text)
        action = await self._judge_block(descriptor, state.builder.committed_descriptors, context)
        if action.judge_failed:
            state.judge_error_occurred = True

        if action.action == "pass":
            return state.builder.commit_text(text)
        if action.action == "replace":
            return self._apply_replacement_to_builder(state.builder, action)
        return []  # blocked text — suppress

    async def _handle_tool_stop(
        self,
        state: _SimpleLLMAnthropicState,
        tool: BufferedTool,
        context: "PolicyContext",
    ) -> list[MessageStreamEvent]:
        """Dispatch the judge concurrently; the decision is applied at message_delta.

        Tools all buffer until finalize anyway, so awaiting the judge here
        would just serialize N round-trips for no incremental wire gain.
        Submitting to the orchestrator returns immediately; the actual
        coroutine starts on the next event-loop tick and runs alongside
        sibling tool judges. Order-preserving collection happens in
        `_handle_message_delta`.
        """
        descriptor = BlockDescriptor(type="tool_use", content=f"{tool.name}({json.dumps(tool.parsed_input)})")
        coro = self._judge_block(descriptor, state.builder.committed_descriptors, context)
        state.tool_judge.submit(_PendingTool(tool=tool), coro)
        return []

    async def _handle_message_delta(
        self, event: RawMessageDeltaEvent, context: "PolicyContext"
    ) -> list[MessageStreamEvent]:
        """Collect concurrent tool-judge results, apply decisions, then flush.

        Walks the orchestrator's results in submission order. A `Bailed`
        item — a tool whose judge was cancelled because an earlier tool
        was blocked — is recorded as blocked in the consolidated marker.
        Each non-bailed `pass` becomes `builder.buffer_tool`, `replace`
        becomes the appropriate commit chain, `block` becomes
        `record_blocked_tool`. Any judge failure flips
        `judge_error_occurred`, which surfaces as the warning (on_error
        pass) or fallback (on_error block) at finalize time.
        """
        state = self._anthropic_state(context)

        events: list[MessageStreamEvent] = []
        for item, result in await state.tool_judge.collect():
            events.extend(self._apply_tool_decision(state, item.tool, result))

        if state.judge_error_occurred:
            if self._config.on_error == "pass":
                state.builder.note_judge_unavailable(JUDGE_UNAVAILABLE_WARNING)
            else:
                state.builder.set_fallback_text(JUDGE_ERROR_BLOCKED_MESSAGE)

        events.extend(state.builder.finalize(event))
        return events

    def _apply_tool_decision(
        self,
        state: _SimpleLLMAnthropicState,
        tool: BufferedTool,
        result: JudgeAction | Bailed,
    ) -> list[MessageStreamEvent]:
        if isinstance(result, Bailed):
            # An earlier tool was blocked; this one's judge was cancelled
            # before completing. Surface in the consolidated marker so the
            # next turn can see what was attempted.
            state.builder.record_blocked_tool(tool.name, judge_failed=False)
            return []

        if result.judge_failed:
            state.judge_error_occurred = True

        if result.action == "pass":
            state.builder.buffer_tool(id=tool.id, name=tool.name, input_json=tool.input_json)
            return []
        if result.action == "replace":
            return self._apply_replacement_to_builder(state.builder, result)
        # "block"
        state.builder.record_blocked_tool(tool.name, judge_failed=result.judge_failed)
        return []

    async def on_anthropic_streaming_policy_complete(self, context: "PolicyContext") -> None:
        """Clean up per-request Anthropic state."""
        context.pop_request_state(self, _SimpleLLMAnthropicState)


__all__ = ["SimpleLLMPolicy", "SimpleLLMJudgeConfig"]
