"""Unit tests for SimpleLLMPolicy.

Tests cover all four execution paths:
- Anthropic non-streaming and streaming
- OpenAI non-streaming and streaming
Plus config/init and freeze_configured_state.
"""

from __future__ import annotations

import logging
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

import pytest
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
)
from litellm.types.utils import (
    ChatCompletionMessageToolCall,
    Choices,
    Delta,
    Function,
    Message,
    ModelResponse,
)
from tests.unit_tests.helpers.litellm_test_utils import make_streaming_chunk

from luthien_proxy.llm.types import Request
from luthien_proxy.policies import PolicyContext
from luthien_proxy.policies.simple_llm_policy import SimpleLLMPolicy
from luthien_proxy.policies.simple_llm_utils import (
    BlockDescriptor,
    JudgeAction,
    ReplacementBlock,
    SimpleLLMJudgeConfig,
)
from luthien_proxy.policy_core import (
    AnthropicExecutionInterface,
    OpenAIPolicyInterface,
)
from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext
from luthien_proxy.streaming.stream_blocks import ContentStreamBlock, ToolCallStreamBlock
from luthien_proxy.streaming.stream_state import StreamState

JUDGE_PATCH = "luthien_proxy.policies.simple_llm_policy.call_simple_llm_judge"

PASS_ACTION = JudgeAction(action="pass")
BLOCK_ACTION = JudgeAction(action="block")


def replace_action(*blocks: ReplacementBlock) -> JudgeAction:
    return JudgeAction(action="replace", blocks=tuple(blocks))


def text_replacement(text: str) -> ReplacementBlock:
    return ReplacementBlock(type="text", text=text)


def tool_replacement(name: str, input_data: dict | None = None) -> ReplacementBlock:
    return ReplacementBlock(type="tool_use", name=name, input=input_data or {})


# ============================================================================
# Test context helpers
# ============================================================================


def make_policy(**overrides) -> SimpleLLMPolicy:
    defaults = {"instructions": "test instructions", "on_error": "pass"}
    defaults.update(overrides)
    return SimpleLLMPolicy(config=defaults)


def make_policy_ctx(transaction_id: str = "test-txn") -> PolicyContext:
    return PolicyContext.for_testing(
        transaction_id=transaction_id,
        request=Request(
            model="test-model",
            messages=[{"role": "user", "content": "test"}],
        ),
    )


def make_anthropic_response(content: list[dict], stop_reason: str = "end_turn") -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": "test-model",
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def make_openai_response(
    content: str | None = None,
    tool_calls: list[ChatCompletionMessageToolCall] | None = None,
    finish_reason: str = "stop",
) -> ModelResponse:
    msg = Message(role="assistant", content=content)
    msg.tool_calls = tool_calls
    return ModelResponse(
        id="resp_test",
        created=1234567890,
        model="test-model",
        object="chat.completion",
        choices=[
            Choices(
                index=0,
                message=msg,
                finish_reason=finish_reason,
            )
        ],
    )


def make_openai_tool_call(name: str = "get_weather", args: str = '{"city": "NYC"}') -> ChatCompletionMessageToolCall:
    return ChatCompletionMessageToolCall(
        id="call_test123",
        function=Function(name=name, arguments=args),
    )


def make_streaming_ctx(
    just_completed=None,
    raw_chunks=None,
    finish_reason=None,
    blocks=None,
) -> StreamingPolicyContext:
    ctx = Mock(spec=StreamingPolicyContext)
    ctx.policy_ctx = make_policy_ctx()
    ctx.original_streaming_response_state = StreamState()
    ctx.original_streaming_response_state.just_completed = just_completed
    ctx.original_streaming_response_state.raw_chunks = raw_chunks or []
    if finish_reason:
        ctx.original_streaming_response_state.finish_reason = finish_reason
    if blocks:
        ctx.original_streaming_response_state.blocks = blocks
    ctx.egress_queue = Mock()
    ctx.egress_queue.put_nowait = Mock()
    ctx.egress_queue.put = AsyncMock()
    return ctx


# ============================================================================
# Config and freeze tests
# ============================================================================


class TestConfigAndFreeze:
    def test_from_dict_config(self):
        policy = SimpleLLMPolicy(config={"instructions": "be nice"})
        assert policy._config.instructions == "be nice"
        assert policy._config.on_error == "pass"

    def test_from_pydantic_config(self):
        cfg = SimpleLLMJudgeConfig(instructions="test", temperature=0.5)
        policy = SimpleLLMPolicy(config=cfg)
        assert policy._config.temperature == 0.5

    def test_required_instructions(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SimpleLLMPolicy(config={"on_error": "pass"})

    def test_invalid_on_error(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SimpleLLMPolicy(config={"instructions": "x", "on_error": "maybe"})

    def test_fail_open_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            make_policy(on_error="pass")
        assert "on_error='pass'" in caplog.text

    def test_fail_secure_no_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            make_policy(on_error="block")
        assert "on_error='pass'" not in caplog.text

    def test_freeze_configured_state(self):
        policy = make_policy()
        policy.freeze_configured_state()  # should not raise

    def test_short_policy_name(self):
        assert make_policy().short_policy_name == "SimpleLLM"

    def test_implements_interfaces(self):
        policy = make_policy()
        assert isinstance(policy, OpenAIPolicyInterface)
        assert isinstance(policy, AnthropicExecutionInterface)


# ============================================================================
# _resolve_judge_api_key priority tests (method lives on BasePolicy)
# ============================================================================


class TestResolveJudgeApiKey:
    """Verify the key resolution priority: explicit > passthrough > fallback."""

    def _make_ctx_with_header(self, header: str, value: str) -> PolicyContext:
        from luthien_proxy.types import RawHttpRequest

        return PolicyContext.for_testing(
            raw_http_request=RawHttpRequest(body={}, headers={header: value}),
        )

    def test_explicit_key_takes_priority(self) -> None:
        policy = make_policy()
        ctx = self._make_ctx_with_header("authorization", "Bearer passthrough-key")
        assert policy._resolve_judge_api_key(ctx, "explicit-key", "fallback") == "explicit-key"

    def test_passthrough_bearer_used_when_no_explicit_key(self) -> None:
        policy = make_policy()
        ctx = self._make_ctx_with_header("authorization", "Bearer passthrough-key")
        assert policy._resolve_judge_api_key(ctx, None, "fallback") == "passthrough-key"

    def test_x_api_key_header_used_as_passthrough(self) -> None:
        policy = make_policy()
        ctx = self._make_ctx_with_header("x-api-key", "x-key-value")
        assert policy._resolve_judge_api_key(ctx, None, "fallback") == "x-key-value"

    def test_fallback_used_when_no_passthrough(self) -> None:
        policy = make_policy()
        ctx = PolicyContext.for_testing()
        assert policy._resolve_judge_api_key(ctx, None, "fallback-key") == "fallback-key"

    def test_returns_none_when_no_keys(self) -> None:
        policy = make_policy()
        ctx = PolicyContext.for_testing()
        assert policy._resolve_judge_api_key(ctx, None, None) is None

    def test_empty_bearer_returns_fallback(self) -> None:
        """'Bearer ' with no token should fall through to fallback."""
        policy = make_policy()
        ctx = self._make_ctx_with_header("authorization", "Bearer ")
        assert policy._resolve_judge_api_key(ctx, None, "fallback") == "fallback"

    def test_no_request_returns_fallback(self) -> None:
        """None raw_http_request should fall through to fallback."""
        policy = make_policy()
        ctx = PolicyContext.for_testing(raw_http_request=None)
        assert policy._resolve_judge_api_key(ctx, None, "fallback") == "fallback"

    def test_bearer_takes_priority_over_x_api_key(self) -> None:
        """When both headers present, Authorization Bearer wins."""
        from luthien_proxy.types import RawHttpRequest

        policy = make_policy()
        ctx = PolicyContext.for_testing(
            raw_http_request=RawHttpRequest(
                body={},
                headers={"authorization": "Bearer bearer-key", "x-api-key": "x-key"},
            ),
        )
        assert policy._resolve_judge_api_key(ctx, None, None) == "bearer-key"


# ============================================================================
# Anthropic non-streaming tests
# ============================================================================


class TestAnthropicNonStreaming:
    @pytest.mark.asyncio
    async def test_pass_through_text(self):
        policy = make_policy()
        ctx = make_policy_ctx()
        response = make_anthropic_response([{"type": "text", "text": "hello"}])

        with patch(JUDGE_PATCH, return_value=PASS_ACTION):
            result = await policy.on_anthropic_response(response, ctx)

        assert result["content"] == [{"type": "text", "text": "hello"}]
        assert result["stop_reason"] == "end_turn"

    @pytest.mark.asyncio
    async def test_replace_text_with_text(self):
        policy = make_policy()
        ctx = make_policy_ctx()
        response = make_anthropic_response([{"type": "text", "text": "bad stuff"}])

        action = replace_action(text_replacement("good stuff"))
        with patch(JUDGE_PATCH, return_value=action):
            result = await policy.on_anthropic_response(response, ctx)

        assert result["content"] == [{"type": "text", "text": "good stuff"}]

    @pytest.mark.asyncio
    async def test_replace_tool_use_with_text(self):
        policy = make_policy()
        ctx = make_policy_ctx()
        response = make_anthropic_response(
            [{"type": "tool_use", "id": "toolu_abc", "name": "dangerous", "input": {}}],
            stop_reason="tool_use",
        )

        action = replace_action(text_replacement("I can't do that"))
        with patch(JUDGE_PATCH, return_value=action):
            result = await policy.on_anthropic_response(response, ctx)

        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        assert result["content"][0]["text"] == "I can't do that"
        assert result["stop_reason"] == "end_turn"

    @pytest.mark.asyncio
    async def test_replace_text_with_tool_use(self):
        policy = make_policy()
        ctx = make_policy_ctx()
        response = make_anthropic_response(
            [{"type": "text", "text": "let me help"}],
        )

        action = replace_action(tool_replacement("search", {"q": "test"}))
        with patch(JUDGE_PATCH, return_value=action):
            result = await policy.on_anthropic_response(response, ctx)

        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "tool_use"
        assert result["content"][0]["name"] == "search"
        assert result["content"][0]["input"] == {"q": "test"}
        assert result["stop_reason"] == "tool_use"

    @pytest.mark.asyncio
    async def test_multi_block_pass(self):
        policy = make_policy()
        ctx = make_policy_ctx()
        response = make_anthropic_response(
            [
                {"type": "text", "text": "block1"},
                {"type": "text", "text": "block2"},
            ]
        )

        with patch(JUDGE_PATCH, return_value=PASS_ACTION):
            result = await policy.on_anthropic_response(response, ctx)

        assert len(result["content"]) == 2

    @pytest.mark.asyncio
    async def test_replace_with_multiple_blocks(self):
        policy = make_policy()
        ctx = make_policy_ctx()
        response = make_anthropic_response([{"type": "text", "text": "original"}])

        action = replace_action(
            text_replacement("part1"),
            tool_replacement("action", {"key": "val"}),
        )
        with patch(JUDGE_PATCH, return_value=action):
            result = await policy.on_anthropic_response(response, ctx)

        assert len(result["content"]) == 2
        assert result["content"][0]["type"] == "text"
        assert result["content"][1]["type"] == "tool_use"
        assert result["stop_reason"] == "tool_use"

    @pytest.mark.asyncio
    async def test_error_fail_open_injects_warning(self):
        from luthien_proxy.policies.simple_llm_policy import JUDGE_UNAVAILABLE_WARNING

        policy = make_policy(on_error="pass")
        ctx = make_policy_ctx()
        response = make_anthropic_response([{"type": "text", "text": "keep me"}])

        with patch(JUDGE_PATCH, side_effect=RuntimeError("judge down")):
            result = await policy.on_anthropic_response(response, ctx)

        assert len(result["content"]) == 2
        assert result["content"][0] == {"type": "text", "text": "keep me"}
        assert result["content"][1] == {"type": "text", "text": JUDGE_UNAVAILABLE_WARNING}

    @pytest.mark.asyncio
    async def test_error_fail_secure(self):
        policy = make_policy(on_error="block")
        ctx = make_policy_ctx()
        response = make_anthropic_response([{"type": "text", "text": "drop me"}])

        with patch(JUDGE_PATCH, side_effect=RuntimeError("judge down")):
            result = await policy.on_anthropic_response(response, ctx)

        assert len(result["content"]) == 0

    @pytest.mark.asyncio
    async def test_accumulated_context_uses_replacements(self):
        """The second block's judge call should see the replacement from the first."""
        policy = make_policy()
        ctx = make_policy_ctx()
        response = make_anthropic_response(
            [
                {"type": "text", "text": "original1"},
                {"type": "text", "text": "original2"},
            ]
        )

        call_count = 0

        async def judge_side_effect(config, current_block, previous_blocks, api_key=None, extra_headers=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return replace_action(text_replacement("replaced1"))
            # Second call: verify previous_blocks contains the replacement
            assert len(previous_blocks) == 1
            assert previous_blocks[0].content == "replaced1"
            return PASS_ACTION

        with patch(JUDGE_PATCH, side_effect=judge_side_effect):
            await policy.on_anthropic_response(response, ctx)

        assert call_count == 2


# ============================================================================
# Anthropic streaming tests
# ============================================================================


class TestAnthropicStreaming:
    @pytest.mark.asyncio
    async def test_text_block_pass_through(self):
        """Text start passes, deltas buffered, stop triggers judge + emits delta+stop."""
        policy = make_policy()
        ctx = make_policy_ctx()

        # Start event - should pass through
        text_block = TextBlock(type="text", text="")
        start = RawContentBlockStartEvent(type="content_block_start", index=0, content_block=text_block)
        events = await policy.on_anthropic_stream_event(start, ctx)
        assert len(events) == 1  # start passes through

        # Delta - should be buffered
        delta = TextDelta(type="text_delta", text="hello world")
        delta_event = RawContentBlockDeltaEvent(type="content_block_delta", index=0, delta=delta)
        events = await policy.on_anthropic_stream_event(delta_event, ctx)
        assert events == []  # buffered

        # Stop - should trigger judge and emit delta + stop
        stop = RawContentBlockStopEvent(type="content_block_stop", index=0)
        with patch(JUDGE_PATCH, return_value=PASS_ACTION):
            events = await policy.on_anthropic_stream_event(stop, ctx)

        assert len(events) == 2  # delta + stop
        assert isinstance(events[0], RawContentBlockDeltaEvent)
        assert isinstance(events[0].delta, TextDelta)
        assert events[0].delta.text == "hello world"
        assert isinstance(events[1], RawContentBlockStopEvent)

    @pytest.mark.asyncio
    async def test_text_block_replaced(self):
        policy = make_policy()
        ctx = make_policy_ctx()

        # Start
        text_block = TextBlock(type="text", text="")
        start = RawContentBlockStartEvent(type="content_block_start", index=0, content_block=text_block)
        await policy.on_anthropic_stream_event(start, ctx)

        # Delta
        delta = TextDelta(type="text_delta", text="bad content")
        delta_event = RawContentBlockDeltaEvent(type="content_block_delta", index=0, delta=delta)
        await policy.on_anthropic_stream_event(delta_event, ctx)

        # Stop with replacement
        stop = RawContentBlockStopEvent(type="content_block_stop", index=0)
        action = replace_action(text_replacement("good content"))
        with patch(JUDGE_PATCH, return_value=action):
            events = await policy.on_anthropic_stream_event(stop, ctx)

        # Should emit: new start + delta + stop
        assert len(events) == 3
        assert isinstance(events[0], RawContentBlockStartEvent)
        assert isinstance(events[0].content_block, TextBlock)
        assert isinstance(events[1], RawContentBlockDeltaEvent)
        assert isinstance(events[1].delta, TextDelta)
        assert events[1].delta.text == "good content"
        assert isinstance(events[2], RawContentBlockStopEvent)

    @pytest.mark.asyncio
    async def test_tool_use_replaced_with_text(self):
        """Tool start buffered, deltas buffered, stop emits text block events."""
        policy = make_policy()
        ctx = make_policy_ctx()

        # Start - tool_use should be suppressed
        tool_block = ToolUseBlock(type="tool_use", id="toolu_abc", name="danger", input={})
        start = RawContentBlockStartEvent(type="content_block_start", index=0, content_block=tool_block)
        events = await policy.on_anthropic_stream_event(start, ctx)
        assert events == []  # suppressed

        # Delta - should be buffered
        json_delta = InputJSONDelta(type="input_json_delta", partial_json='{"key": "val"}')
        delta_event = RawContentBlockDeltaEvent(type="content_block_delta", index=0, delta=json_delta)
        events = await policy.on_anthropic_stream_event(delta_event, ctx)
        assert events == []

        # Stop - judge replaces with text
        stop = RawContentBlockStopEvent(type="content_block_stop", index=0)
        action = replace_action(text_replacement("blocked"))
        with patch(JUDGE_PATCH, return_value=action):
            events = await policy.on_anthropic_stream_event(stop, ctx)

        # Should emit text start + text delta + stop
        assert len(events) == 3
        assert isinstance(events[0], RawContentBlockStartEvent)
        assert isinstance(events[0].content_block, TextBlock)
        assert isinstance(events[1], RawContentBlockDeltaEvent)
        assert isinstance(events[1].delta, TextDelta)
        assert events[1].delta.text == "blocked"

    @pytest.mark.asyncio
    async def test_tool_use_pass_through(self):
        """Tool blocks that pass get reconstructed: start + json delta + stop."""
        policy = make_policy()
        ctx = make_policy_ctx()

        # Start - suppressed
        tool_block = ToolUseBlock(type="tool_use", id="toolu_abc", name="safe", input={})
        start = RawContentBlockStartEvent(type="content_block_start", index=0, content_block=tool_block)
        await policy.on_anthropic_stream_event(start, ctx)

        # Delta
        json_delta = InputJSONDelta(type="input_json_delta", partial_json='{"x": 1}')
        delta_event = RawContentBlockDeltaEvent(type="content_block_delta", index=0, delta=json_delta)
        await policy.on_anthropic_stream_event(delta_event, ctx)

        # Stop - pass
        stop = RawContentBlockStopEvent(type="content_block_stop", index=0)
        with patch(JUDGE_PATCH, return_value=PASS_ACTION):
            events = await policy.on_anthropic_stream_event(stop, ctx)

        # Reconstructed: start + json delta + stop
        assert len(events) == 3
        assert isinstance(events[0], RawContentBlockStartEvent)
        assert isinstance(events[0].content_block, ToolUseBlock)
        assert events[0].content_block.name == "safe"
        assert isinstance(events[1], RawContentBlockDeltaEvent)
        assert isinstance(events[1].delta, InputJSONDelta)
        assert events[1].delta.partial_json == '{"x": 1}'
        assert isinstance(events[2], RawContentBlockStopEvent)

    @pytest.mark.asyncio
    async def test_non_content_events_pass_through(self):
        policy = make_policy()
        ctx = make_policy_ctx()

        # message_start should pass through without judge call
        msg_start = Mock(spec=RawMessageStartEvent)
        events = await policy.on_anthropic_stream_event(msg_start, ctx)
        assert events == [msg_start]

        msg_stop = RawMessageStopEvent(type="message_stop")
        events = await policy.on_anthropic_stream_event(msg_stop, ctx)
        assert events == [msg_stop]

    @pytest.mark.asyncio
    async def test_streaming_judge_error_injects_warning(self):
        """When judge fails on a text block and on_error='pass', the warning
        block is emitted before message_delta (not message_stop) to maintain
        valid Anthropic streaming event ordering."""
        from anthropic.types import MessageDeltaUsage
        from anthropic.types.raw_message_delta_event import Delta

        from luthien_proxy.policies.simple_llm_policy import JUDGE_UNAVAILABLE_WARNING

        policy = make_policy(on_error="pass")
        ctx = make_policy_ctx()

        # Send a text block through that triggers a judge error
        text_block = TextBlock(type="text", text="")
        start = RawContentBlockStartEvent(type="content_block_start", index=0, content_block=text_block)
        await policy.on_anthropic_stream_event(start, ctx)

        delta = TextDelta(type="text_delta", text="content")
        delta_event = RawContentBlockDeltaEvent(type="content_block_delta", index=0, delta=delta)
        await policy.on_anthropic_stream_event(delta_event, ctx)

        stop = RawContentBlockStopEvent(type="content_block_stop", index=0)
        with patch(JUDGE_PATCH, side_effect=RuntimeError("judge down")):
            await policy.on_anthropic_stream_event(stop, ctx)

        # Send message_delta — warning events should be injected BEFORE it
        msg_delta = RawMessageDeltaEvent(
            type="message_delta",
            delta=Delta(stop_reason="end_turn", stop_sequence=None),
            usage=MessageDeltaUsage(output_tokens=10),
        )
        events = await policy.on_anthropic_stream_event(msg_delta, ctx)

        # Warning: start + delta + stop, then the original message_delta
        assert len(events) == 4
        assert isinstance(events[0], RawContentBlockStartEvent)
        assert isinstance(events[1], RawContentBlockDeltaEvent)
        assert isinstance(events[1].delta, TextDelta)
        assert events[1].delta.text == JUDGE_UNAVAILABLE_WARNING
        assert isinstance(events[2], RawContentBlockStopEvent)
        assert isinstance(events[3], RawMessageDeltaEvent)

    @pytest.mark.asyncio
    async def test_streaming_judge_error_block_no_warning(self):
        """When judge fails and on_error='block', no warning is injected."""
        from anthropic.types import MessageDeltaUsage
        from anthropic.types.raw_message_delta_event import Delta

        policy = make_policy(on_error="block")
        ctx = make_policy_ctx()

        # Text block with judge failure
        text_block = TextBlock(type="text", text="")
        start = RawContentBlockStartEvent(type="content_block_start", index=0, content_block=text_block)
        await policy.on_anthropic_stream_event(start, ctx)

        delta = TextDelta(type="text_delta", text="content")
        delta_event = RawContentBlockDeltaEvent(type="content_block_delta", index=0, delta=delta)
        await policy.on_anthropic_stream_event(delta_event, ctx)

        stop = RawContentBlockStopEvent(type="content_block_stop", index=0)
        with patch(JUDGE_PATCH, side_effect=RuntimeError("judge down")):
            await policy.on_anthropic_stream_event(stop, ctx)

        # message_delta should pass through without warning
        msg_delta = RawMessageDeltaEvent(
            type="message_delta",
            delta=Delta(stop_reason="end_turn", stop_sequence=None),
            usage=MessageDeltaUsage(output_tokens=10),
        )
        events = await policy.on_anthropic_stream_event(msg_delta, ctx)
        assert len(events) == 1
        assert isinstance(events[0], RawMessageDeltaEvent)

    @pytest.mark.asyncio
    async def test_streaming_parallel_tool_use_with_judge_failure(self):
        """Parallel tool_use blocks with judge failure should produce valid
        event ordering: all content blocks before message_delta, warning
        before message_delta, and corrected stop_reason."""
        from anthropic.types import MessageDeltaUsage
        from anthropic.types.raw_message_delta_event import Delta

        from luthien_proxy.policies.simple_llm_policy import JUDGE_UNAVAILABLE_WARNING

        policy = make_policy(on_error="pass")
        ctx = make_policy_ctx()

        all_events: list = []

        # Simulate 2 parallel tool_use blocks from backend
        for i in range(2):
            tool_block = ToolUseBlock(type="tool_use", id=f"toolu_{i}", name=f"tool_{i}", input={})
            start = RawContentBlockStartEvent(type="content_block_start", index=i, content_block=tool_block)
            all_events.extend(await policy.on_anthropic_stream_event(start, ctx))

        for i in range(2):
            json_delta = InputJSONDelta(type="input_json_delta", partial_json='{"key": "val"}')
            delta_event = RawContentBlockDeltaEvent(type="content_block_delta", index=i, delta=json_delta)
            all_events.extend(await policy.on_anthropic_stream_event(delta_event, ctx))

        # First tool_use: judge passes. Second: judge fails.
        stop0 = RawContentBlockStopEvent(type="content_block_stop", index=0)
        with patch(JUDGE_PATCH, return_value=PASS_ACTION):
            all_events.extend(await policy.on_anthropic_stream_event(stop0, ctx))

        stop1 = RawContentBlockStopEvent(type="content_block_stop", index=1)
        with patch(JUDGE_PATCH, side_effect=RuntimeError("judge down")):
            all_events.extend(await policy.on_anthropic_stream_event(stop1, ctx))

        # message_delta — warning should be injected BEFORE this event
        msg_delta = RawMessageDeltaEvent(
            type="message_delta",
            delta=Delta(stop_reason="tool_use", stop_sequence=None),
            usage=MessageDeltaUsage(output_tokens=50),
        )
        all_events.extend(await policy.on_anthropic_stream_event(msg_delta, ctx))

        # Verify all content blocks come before message_delta
        msg_delta_idx = None
        last_content_block_idx = None
        for idx, evt in enumerate(all_events):
            if isinstance(evt, (RawContentBlockStartEvent, RawContentBlockDeltaEvent, RawContentBlockStopEvent)):
                last_content_block_idx = idx
            if isinstance(evt, RawMessageDeltaEvent):
                msg_delta_idx = idx

        assert msg_delta_idx is not None, "Expected message_delta event"
        assert last_content_block_idx is not None, "Expected content block events"
        assert last_content_block_idx < msg_delta_idx, (
            "Content blocks must come before message_delta for valid Anthropic streaming"
        )

        # Verify the warning was emitted
        warning_texts = [
            evt.delta.text
            for evt in all_events
            if isinstance(evt, RawContentBlockDeltaEvent) and isinstance(evt.delta, TextDelta)
        ]
        assert JUDGE_UNAVAILABLE_WARNING in warning_texts

        # Verify stop_reason is still "tool_use" (since tool_use blocks were emitted)
        final_delta = all_events[msg_delta_idx]
        assert isinstance(final_delta, RawMessageDeltaEvent)
        assert final_delta.delta.stop_reason == "tool_use"

    @pytest.mark.asyncio
    async def test_streaming_stop_reason_corrected_when_tools_blocked(self):
        """When all tool_use blocks are blocked, stop_reason should change
        from 'tool_use' to 'end_turn'."""
        from anthropic.types import MessageDeltaUsage
        from anthropic.types.raw_message_delta_event import Delta

        policy = make_policy(on_error="block")
        ctx = make_policy_ctx()

        # Single tool_use block that gets blocked by the judge
        tool_block = ToolUseBlock(type="tool_use", id="toolu_0", name="dangerous_tool", input={})
        start = RawContentBlockStartEvent(type="content_block_start", index=0, content_block=tool_block)
        await policy.on_anthropic_stream_event(start, ctx)

        json_delta = InputJSONDelta(type="input_json_delta", partial_json="{}")
        delta_event = RawContentBlockDeltaEvent(type="content_block_delta", index=0, delta=json_delta)
        await policy.on_anthropic_stream_event(delta_event, ctx)

        stop = RawContentBlockStopEvent(type="content_block_stop", index=0)
        with patch(JUDGE_PATCH, return_value=BLOCK_ACTION):
            await policy.on_anthropic_stream_event(stop, ctx)

        # message_delta has stop_reason="tool_use" from backend, but should
        # be corrected to "end_turn" since no tool_use blocks were emitted
        msg_delta = RawMessageDeltaEvent(
            type="message_delta",
            delta=Delta(stop_reason="tool_use", stop_sequence=None),
            usage=MessageDeltaUsage(output_tokens=10),
        )
        events = await policy.on_anthropic_stream_event(msg_delta, ctx)

        assert len(events) == 1
        assert isinstance(events[0], RawMessageDeltaEvent)
        assert events[0].delta.stop_reason == "end_turn"


# ============================================================================
# OpenAI non-streaming tests
# ============================================================================


class TestOpenAINonStreaming:
    @pytest.mark.asyncio
    async def test_pass_through_text(self):
        policy = make_policy()
        ctx = make_policy_ctx()
        response = make_openai_response(content="hello")

        with patch(JUDGE_PATCH, return_value=PASS_ACTION):
            result = await policy.on_openai_response(response, ctx)

        choice = cast(Choices, result.choices[0])
        assert choice.message.content == "hello"
        assert choice.finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_replace_text(self):
        policy = make_policy()
        ctx = make_policy_ctx()
        response = make_openai_response(content="bad")

        action = replace_action(text_replacement("good"))
        with patch(JUDGE_PATCH, return_value=action):
            result = await policy.on_openai_response(response, ctx)

        choice = cast(Choices, result.choices[0])
        assert choice.message.content == "good"

    @pytest.mark.asyncio
    async def test_replace_tool_call_with_text(self):
        policy = make_policy()
        ctx = make_policy_ctx()
        tc = make_openai_tool_call()
        response = make_openai_response(tool_calls=[tc], finish_reason="tool_calls")

        action = replace_action(text_replacement("nope"))
        with patch(JUDGE_PATCH, return_value=action):
            result = await policy.on_openai_response(response, ctx)

        choice = cast(Choices, result.choices[0])
        assert choice.message.content == "nope"
        assert choice.message.tool_calls is None
        assert choice.finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_error_fail_open_injects_warning(self):
        from luthien_proxy.policies.simple_llm_policy import JUDGE_UNAVAILABLE_WARNING

        policy = make_policy(on_error="pass")
        ctx = make_policy_ctx()
        response = make_openai_response(content="keep me")

        with patch(JUDGE_PATCH, side_effect=RuntimeError("judge down")):
            result = await policy.on_openai_response(response, ctx)

        choice = cast(Choices, result.choices[0])
        assert choice.message.content == f"keep me{JUDGE_UNAVAILABLE_WARNING}"

    @pytest.mark.asyncio
    async def test_pass_through_tool_call(self):
        policy = make_policy()
        ctx = make_policy_ctx()
        tc = make_openai_tool_call()
        response = make_openai_response(tool_calls=[tc], finish_reason="tool_calls")

        with patch(JUDGE_PATCH, return_value=PASS_ACTION):
            result = await policy.on_openai_response(response, ctx)

        choice = cast(Choices, result.choices[0])
        assert choice.message.tool_calls is not None
        assert len(choice.message.tool_calls) == 1
        assert choice.finish_reason == "tool_calls"


# ============================================================================
# OpenAI streaming tests
# ============================================================================


class TestOpenAIStreaming:
    @pytest.mark.asyncio
    async def test_content_complete_pass_through(self):
        policy = make_policy()
        content_block = ContentStreamBlock(id="content")
        content_block.content = "hello world"
        content_block.is_complete = True

        last_chunk = make_streaming_chunk(content=None, finish_reason="stop")
        ctx = make_streaming_ctx(
            just_completed=content_block,
            raw_chunks=[last_chunk],
        )

        with patch(JUDGE_PATCH, return_value=PASS_ACTION):
            await policy.on_content_complete(ctx)

        # Should have sent text chunk + finish chunk
        assert ctx.egress_queue.put.call_count >= 1
        first_chunk = ctx.egress_queue.put.call_args_list[0][0][0]
        delta = first_chunk.choices[0].delta
        assert isinstance(delta, Delta)
        assert delta.content == "hello world"

    @pytest.mark.asyncio
    async def test_content_complete_replaced(self):
        policy = make_policy()
        content_block = ContentStreamBlock(id="content")
        content_block.content = "bad"
        content_block.is_complete = True

        last_chunk = make_streaming_chunk(content=None, finish_reason="stop")
        ctx = make_streaming_ctx(
            just_completed=content_block,
            raw_chunks=[last_chunk],
        )

        action = replace_action(text_replacement("good"))
        with patch(JUDGE_PATCH, return_value=action):
            await policy.on_content_complete(ctx)

        assert ctx.egress_queue.put.call_count >= 1
        first_chunk = ctx.egress_queue.put.call_args_list[0][0][0]
        delta = first_chunk.choices[0].delta
        assert delta.content == "good"

    @pytest.mark.asyncio
    async def test_tool_call_complete_replaced_with_text(self):
        policy = make_policy()
        tc_block = ToolCallStreamBlock(id="call_abc", index=0, name="danger", arguments='{"x": 1}')
        tc_block.is_complete = True

        ctx = make_streaming_ctx(just_completed=tc_block)

        action = replace_action(text_replacement("blocked"))
        with patch(JUDGE_PATCH, return_value=action):
            await policy.on_tool_call_complete(ctx)

        assert ctx.egress_queue.put.call_count >= 1
        first_chunk = ctx.egress_queue.put.call_args_list[0][0][0]
        delta = first_chunk.choices[0].delta
        assert delta.content == "blocked"

    @pytest.mark.asyncio
    async def test_tool_call_complete_pass_through(self):
        policy = make_policy()
        tc_block = ToolCallStreamBlock(id="call_abc", index=0, name="safe", arguments='{"x": 1}')
        tc_block.is_complete = True

        ctx = make_streaming_ctx(just_completed=tc_block)

        with patch(JUDGE_PATCH, return_value=PASS_ACTION):
            await policy.on_tool_call_complete(ctx)

        assert ctx.egress_queue.put.call_count == 1
        chunk = ctx.egress_queue.put.call_args_list[0][0][0]
        # Tool call chunk should have tool_calls in delta
        delta = chunk.choices[0].delta
        assert delta.tool_calls is not None

    @pytest.mark.asyncio
    async def test_chunk_received_suppressed(self):
        policy = make_policy()
        ctx = make_streaming_ctx()
        await policy.on_chunk_received(ctx)
        ctx.egress_queue.put.assert_not_called()
        ctx.egress_queue.put_nowait.assert_not_called()

    @pytest.mark.asyncio
    async def test_stream_complete_emits_finish_for_tool_calls(self):
        policy = make_policy()
        tc_block = ToolCallStreamBlock(id="call_abc", index=0, name="safe", arguments="{}")
        tc_block.is_complete = True

        last_chunk = make_streaming_chunk(content=None, finish_reason="tool_calls")
        ctx = make_streaming_ctx(
            finish_reason="tool_calls",
            raw_chunks=[last_chunk],
            blocks=[tc_block],
        )

        # Prime state so it knows there were tool calls
        state = policy._openai_state(ctx)
        state.original_had_tool_use = True
        state.emitted_blocks.append(BlockDescriptor(type="tool_use", content="safe({})"))

        await policy.on_stream_complete(ctx)

        assert ctx.egress_queue.put.call_count == 1
        finish_chunk = ctx.egress_queue.put.call_args_list[0][0][0]
        assert finish_chunk.choices[0].finish_reason == "tool_calls"

    @pytest.mark.asyncio
    async def test_stream_complete_judge_error_injects_warning(self):
        """When judge fails during streaming and on_error='pass', the warning is emitted."""
        from luthien_proxy.policies.simple_llm_policy import JUDGE_UNAVAILABLE_WARNING

        policy = make_policy(on_error="pass")
        content_block = ContentStreamBlock(id="content")
        content_block.content = "hello"
        content_block.is_complete = True

        last_chunk = make_streaming_chunk(content=None, finish_reason="stop")
        ctx = make_streaming_ctx(
            finish_reason="stop",
            raw_chunks=[last_chunk],
        )

        # Simulate judge error state
        state = policy._openai_state(ctx)
        state.judge_error_occurred = True

        await policy.on_stream_complete(ctx)

        # Should have emitted a text chunk with the warning
        assert ctx.egress_queue.put.call_count >= 1
        warning_chunk = ctx.egress_queue.put.call_args_list[0][0][0]
        delta = warning_chunk.choices[0].delta
        assert isinstance(delta, Delta)
        assert JUDGE_UNAVAILABLE_WARNING in (delta.content or "")

    @pytest.mark.asyncio
    async def test_stream_complete_judge_error_block_no_warning(self):
        """When judge fails during streaming and on_error='block', no warning is emitted."""
        policy = make_policy(on_error="block")

        last_chunk = make_streaming_chunk(content=None, finish_reason="stop")
        ctx = make_streaming_ctx(
            finish_reason="stop",
            raw_chunks=[last_chunk],
        )

        # Simulate judge error state with on_error='block'
        state = policy._openai_state(ctx)
        state.judge_error_occurred = True

        await policy.on_stream_complete(ctx)

        # No warning should be emitted
        ctx.egress_queue.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_streaming_policy_complete_cleans_state(self):
        policy = make_policy()
        ctx = make_streaming_ctx()
        # Create state
        policy._openai_state(ctx)
        # Clean up
        await policy.on_streaming_policy_complete(ctx)
        # State should be gone (pop returns None)
        from luthien_proxy.policies.simple_llm_policy import _SimpleLLMOpenAIState

        result = ctx.policy_ctx.pop_request_state(policy, _SimpleLLMOpenAIState)
        assert result is None
