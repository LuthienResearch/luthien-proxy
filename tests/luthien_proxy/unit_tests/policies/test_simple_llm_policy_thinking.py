"""SimpleLLMPolicy handling of thinking and redacted_thinking blocks."""

from __future__ import annotations

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from anthropic.lib.streaming import MessageStreamEvent
from anthropic.types import (
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawMessageDeltaEvent,
    RedactedThinkingBlock,
    SignatureDelta,
    ThinkingBlock,
    ThinkingDelta,
)
from tests.luthien_proxy.fixtures.anthropic_stream_validator import validate_anthropic_event_ordering
from tests.luthien_proxy.fixtures.policy_context import make_policy_context
from tests.luthien_proxy.unit_tests.policies.anthropic_event_builders import (
    block_stop,
    full_stream,
    message_delta,
    text_delta,
    text_start,
    tool_delta,
    tool_start,
)

from luthien_proxy.policies.simple_llm_policy import JUDGE_ERROR_BLOCKED_MESSAGE, SimpleLLMPolicy
from luthien_proxy.policies.simple_llm_utils import BlockDescriptor, JudgeAction, ReplacementBlock, SimpleLLMJudgeConfig
from luthien_proxy.policy_core.anthropic_message_builder import (
    blocked_tools_judge_failed_message,
    dropped_thinking_message,
)

PASS = JudgeAction(action="pass")
DROP = JudgeAction(action="replace", blocks=())


def _policy(judge_thinking: bool = True) -> SimpleLLMPolicy:
    return SimpleLLMPolicy(
        SimpleLLMJudgeConfig(
            instructions="test", on_error="block", auth_provider="user_credentials", judge_thinking=judge_thinking
        )
    )


def _judge(thinking: JudgeAction = PASS, other: JudgeAction = PASS) -> AsyncMock:
    async def decide(descriptor: BlockDescriptor, previous: Any, context: Any) -> JudgeAction:
        return thinking if descriptor.type == "thinking" else other

    return AsyncMock(side_effect=decide)


def _thinking(index: int, text: str, signature: str = "sig") -> list[MessageStreamEvent]:
    return [
        cast(
            MessageStreamEvent,
            RawContentBlockStartEvent(
                type="content_block_start",
                index=index,
                content_block=ThinkingBlock(type="thinking", thinking="", signature=""),
            ),
        ),
        cast(
            MessageStreamEvent,
            RawContentBlockDeltaEvent(
                type="content_block_delta",
                index=index,
                delta=ThinkingDelta(type="thinking_delta", thinking=text),
            ),
        ),
        cast(
            MessageStreamEvent,
            RawContentBlockDeltaEvent(
                type="content_block_delta",
                index=index,
                delta=SignatureDelta(type="signature_delta", signature=signature),
            ),
        ),
        cast(MessageStreamEvent, block_stop(index)),
    ]


def _redacted(index: int) -> list[MessageStreamEvent]:
    start = RawContentBlockStartEvent(
        type="content_block_start",
        index=index,
        content_block=RedactedThinkingBlock(type="redacted_thinking", data="enc"),
    )
    return [cast(MessageStreamEvent, start), cast(MessageStreamEvent, block_stop(index))]


def _tool(index: int, name: str = "Bash") -> list[MessageStreamEvent]:
    return [
        cast(MessageStreamEvent, tool_start(index, tool_id=f"toolu_{index}", name=name)),
        cast(MessageStreamEvent, tool_delta('{"command": "ls"}', index)),
        cast(MessageStreamEvent, block_stop(index)),
    ]


def _text(index: int, text: str) -> list[MessageStreamEvent]:
    return [
        cast(MessageStreamEvent, text_start(index)),
        cast(MessageStreamEvent, text_delta(text, index)),
        cast(MessageStreamEvent, block_stop(index)),
    ]


async def _stream(policy: SimpleLLMPolicy, ctx: Any, events: list[MessageStreamEvent]) -> list[MessageStreamEvent]:
    out: list[MessageStreamEvent] = []
    for event in events:
        out.extend(await policy.on_anthropic_stream_event(event, ctx))
    return out


def _start_types(events: list[MessageStreamEvent]) -> list[str]:
    return [e.content_block.type for e in events if isinstance(e, RawContentBlockStartEvent)]


def _texts(events: list[MessageStreamEvent]) -> list[str]:
    return [
        e.delta.text
        for e in events
        if isinstance(e, RawContentBlockDeltaEvent) and getattr(e.delta, "type", None) == "text_delta"
    ]


def _stop_reason(events: list[MessageStreamEvent]) -> str | None:
    return next(e.delta.stop_reason for e in events if isinstance(e, RawMessageDeltaEvent))


class TestStreaming:
    @pytest.mark.asyncio
    async def test_passed_thinking_is_context_for_later_judges(self):
        policy, ctx = _policy(), make_policy_context()
        judge = _judge()
        with patch.object(policy, "_judge_block", judge):
            await _stream(policy, ctx, [*_thinking(0, "plan: list files"), *_text(1, "done"), message_delta()])

        previous = judge.await_args_list[1].args[1]
        assert previous == (BlockDescriptor(type="thinking", content="plan: list files"),)

    @pytest.mark.asyncio
    async def test_dropped_thinking_blocks_the_tool_call_that_follows(self):
        policy, ctx = _policy(), make_policy_context()
        with patch.object(policy, "_judge_block", _judge(thinking=DROP)):
            out = await _stream(policy, ctx, [*_thinking(0, "bad plan"), *_tool(1), message_delta("tool_use")])

        assert _start_types(out) == ["text", "text"]
        assert dropped_thinking_message(1) in _texts(out)
        assert _stop_reason(out) == "end_turn"
        validate_anthropic_event_ordering(full_stream(out)).assert_valid()

    @pytest.mark.asyncio
    async def test_dropped_thinking_is_recorded(self):
        policy, ctx = _policy(), make_policy_context()
        with (
            patch.object(policy, "_judge_block", _judge(thinking=DROP)),
            patch.object(ctx, "record_event") as record,
        ):
            out = await _stream(policy, ctx, [*_thinking(0, "bad plan"), *_text(1, "hi"), message_delta()])

        assert [call.args[0] for call in record.call_args_list] == ["policy.simple_llm.thinking_dropped"]
        assert _start_types(out) == ["text", "text"]

    @pytest.mark.asyncio
    async def test_interleaved_thinking_keeps_upstream_order(self):
        policy, ctx = _policy(), make_policy_context()
        events = [
            *_thinking(0, "a"),
            *_tool(1),
            *_thinking(2, "b"),
            *_redacted(3),
            *_tool(4),
            message_delta("tool_use"),
        ]
        with patch.object(policy, "_judge_block", _judge()):
            out = await _stream(policy, ctx, events)

        assert _start_types(out) == ["thinking", "tool_use", "thinking", "redacted_thinking", "tool_use"]
        assert _stop_reason(out) == "tool_use"
        validate_anthropic_event_ordering(full_stream(out)).assert_valid()

    @pytest.mark.asyncio
    async def test_dropped_thinking_after_a_tool_blocks_the_tools(self):
        policy, ctx = _policy(), make_policy_context()
        judge = AsyncMock(side_effect=[PASS, DROP, PASS])
        events = [*_tool(0), *_thinking(1, "b"), *_tool(2), message_delta("tool_use")]
        with patch.object(policy, "_judge_block", judge):
            out = await _stream(policy, ctx, events)

        assert "tool_use" not in _start_types(out)
        assert _stop_reason(out) == "end_turn"
        validate_anthropic_event_ordering(full_stream(out)).assert_valid()

    @pytest.mark.asyncio
    async def test_reasoning_never_trails_the_last_tool_call(self):
        policy, ctx = _policy(), make_policy_context()
        replace_read = JudgeAction(action="replace", blocks=(ReplacementBlock(type="text", text="no Read"),))

        async def decide(descriptor: BlockDescriptor, previous: Any, context: Any) -> JudgeAction:
            return replace_read if descriptor.content.startswith("Read(") else PASS

        events = [*_thinking(0, "a"), *_tool(1), *_thinking(2, "b"), *_tool(3, name="Read"), message_delta("tool_use")]
        with patch.object(policy, "_judge_block", AsyncMock(side_effect=decide)):
            out = await _stream(policy, ctx, events)

        assert _start_types(out) == ["thinking", "text", "thinking", "tool_use"]
        validate_anthropic_event_ordering(full_stream(out)).assert_valid()

    @pytest.mark.asyncio
    async def test_judge_failure_on_thinking_keeps_the_error_message(self):
        policy, ctx = _policy(), make_policy_context()
        failed = JudgeAction(action="block", judge_failed=True)
        with patch.object(policy, "_judge_block", _judge(thinking=failed)):
            out = await _stream(policy, ctx, [*_thinking(0, "plan"), message_delta()])

        assert _texts(out) == [JUDGE_ERROR_BLOCKED_MESSAGE, dropped_thinking_message(1, judge_failed=True)]

    @pytest.mark.asyncio
    async def test_thinking_cancelled_by_a_failed_judge_counts_as_a_judge_failure(self):
        policy, ctx = _policy(), make_policy_context()
        never = asyncio.Event()

        async def decide(descriptor: BlockDescriptor, previous: Any, context: Any) -> JudgeAction:
            if descriptor.type == "thinking":
                await never.wait()
            return JudgeAction(action="block", judge_failed=True) if descriptor.content.startswith("Read(") else PASS

        events = [*_tool(0), *_thinking(1, "b"), *_tool(2, name="Read"), message_delta("tool_use")]
        with patch.object(policy, "_judge_block", AsyncMock(side_effect=decide)):
            out = await _stream(policy, ctx, events)

        assert _texts(out) == [
            dropped_thinking_message(1, judge_failed=True),
            blocked_tools_judge_failed_message(["Read", "Bash"]),
        ]
        assert _stop_reason(out) == "end_turn"

    @pytest.mark.asyncio
    async def test_judge_thinking_off_streams_thinking_live(self):
        policy, ctx = _policy(judge_thinking=False), make_policy_context()
        judge = _judge()
        with patch.object(policy, "_judge_block", judge):
            first = await policy.on_anthropic_stream_event(_thinking(0, "x")[0], ctx)

        assert _start_types(first) == ["thinking"]
        judge.assert_not_awaited()


class TestNonStreaming:
    @pytest.mark.asyncio
    async def test_judge_failure_on_thinking_keeps_the_error_message(self):
        policy, ctx = _policy(), make_policy_context()
        failed = JudgeAction(action="block", judge_failed=True)
        response: dict[str, Any] = {
            "content": [{"type": "thinking", "thinking": "plan", "signature": "sig"}],
            "stop_reason": "end_turn",
        }
        with patch.object(policy, "_judge_block", _judge(thinking=failed)):
            result = await policy.on_anthropic_response(response, ctx)

        assert [block["text"] for block in result["content"]] == [
            JUDGE_ERROR_BLOCKED_MESSAGE,
            dropped_thinking_message(1, judge_failed=True),
        ]

    @pytest.mark.asyncio
    async def test_thinking_is_judged_and_kept_when_passed(self):
        policy, ctx = _policy(), make_policy_context()
        thinking = {"type": "thinking", "thinking": "plan", "signature": "sig"}
        response: dict[str, Any] = {"content": [thinking, {"type": "text", "text": "hi"}], "stop_reason": "end_turn"}
        judge = _judge()
        with patch.object(policy, "_judge_block", judge):
            result = await policy.on_anthropic_response(response, ctx)

        assert result["content"] == [thinking, {"type": "text", "text": "hi"}]
        assert judge.await_args_list[0].args[0] == BlockDescriptor(type="thinking", content="plan")

    @pytest.mark.asyncio
    async def test_dropped_thinking_blocks_tool_and_leaves_marker(self):
        policy, ctx = _policy(), make_policy_context()
        response: dict[str, Any] = {
            "content": [
                {"type": "thinking", "thinking": "bad plan", "signature": "sig"},
                {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls"}},
            ],
            "stop_reason": "tool_use",
        }
        with patch.object(policy, "_judge_block", _judge(thinking=DROP)):
            result = await policy.on_anthropic_response(response, ctx)

        assert [block["type"] for block in result["content"]] == ["text", "text"]
        assert result["content"][0]["text"] == dropped_thinking_message(1)
        assert result["stop_reason"] == "end_turn"

    @pytest.mark.asyncio
    async def test_interleaved_thinking_keeps_upstream_order(self):
        policy, ctx = _policy(), make_policy_context()
        content = [
            {"type": "thinking", "thinking": "a", "signature": "s1"},
            {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {}},
            {"type": "redacted_thinking", "data": "enc"},
            {"type": "thinking", "thinking": "b", "signature": "s2"},
            {"type": "tool_use", "id": "toolu_2", "name": "Read", "input": {}},
        ]
        with patch.object(policy, "_judge_block", _judge()):
            result = await policy.on_anthropic_response({"content": content, "stop_reason": "tool_use"}, ctx)

        assert [block["type"] for block in result["content"]] == [block["type"] for block in content]

    @pytest.mark.asyncio
    async def test_judge_thinking_off_keeps_reasoning_out_of_judge_context(self):
        policy, ctx = _policy(judge_thinking=False), make_policy_context()
        thinking = {"type": "thinking", "thinking": "plan", "signature": "sig"}
        judge = _judge()
        with patch.object(policy, "_judge_block", judge):
            await policy.on_anthropic_response(
                {"content": [thinking, {"type": "text", "text": "hi"}], "stop_reason": "end_turn"}, ctx
            )

        assert judge.await_args_list[0].args[1] == (BlockDescriptor(type="thinking", content=""),)

    @pytest.mark.asyncio
    async def test_judge_thinking_off_keeps_thinking_unjudged(self):
        policy, ctx = _policy(judge_thinking=False), make_policy_context()
        thinking = {"type": "thinking", "thinking": "plan", "signature": "sig"}
        judge = _judge()
        with patch.object(policy, "_judge_block", judge):
            result = await policy.on_anthropic_response({"content": [thinking], "stop_reason": "end_turn"}, ctx)

        assert result["content"] == [thinking]
        judge.assert_not_awaited()
