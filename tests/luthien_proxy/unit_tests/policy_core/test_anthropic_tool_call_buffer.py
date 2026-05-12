"""Unit tests for ToolCallStreamBuffer and transform_anthropic_response."""

from __future__ import annotations

from typing import cast

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
from anthropic.types.raw_message_delta_event import Delta as MessageDeltaPayload
from anthropic.types.raw_message_start_event import Message as RawMessage

from luthien_proxy.llm.types.anthropic import AnthropicContentBlock, AnthropicResponse
from luthien_proxy.policy_core import (
    BufferedToolCall,
    ToolCallStreamBuffer,
    transform_anthropic_response,
)


def _text_start(index: int) -> RawContentBlockStartEvent:
    return RawContentBlockStartEvent(
        type="content_block_start", index=index, content_block=TextBlock(type="text", text="")
    )


def _text_delta(index: int, text: str) -> RawContentBlockDeltaEvent:
    return RawContentBlockDeltaEvent(
        type="content_block_delta",
        index=index,
        delta=TextDelta(type="text_delta", text=text),
    )


def _block_stop(index: int) -> RawContentBlockStopEvent:
    return RawContentBlockStopEvent(type="content_block_stop", index=index)


def _tool_start(index: int, tool_id: str, name: str) -> RawContentBlockStartEvent:
    return RawContentBlockStartEvent(
        type="content_block_start",
        index=index,
        content_block=ToolUseBlock(type="tool_use", id=tool_id, name=name, input={}),
    )


def _json_delta(index: int, partial: str) -> RawContentBlockDeltaEvent:
    return RawContentBlockDeltaEvent(
        type="content_block_delta",
        index=index,
        delta=InputJSONDelta(type="input_json_delta", partial_json=partial),
    )


def _message_delta(stop_reason: str | None) -> RawMessageDeltaEvent:
    return RawMessageDeltaEvent(
        type="message_delta",
        delta=MessageDeltaPayload(stop_reason=stop_reason, stop_sequence=None),
        usage={"input_tokens": 10, "output_tokens": 5},  # type: ignore[arg-type]
    )


async def _passthrough(tool_calls: list[BufferedToolCall]) -> list[AnthropicContentBlock]:
    return [tc.as_content_block() for tc in tool_calls]


async def _block_all(_: list[BufferedToolCall]) -> list[AnthropicContentBlock]:
    return [cast(AnthropicContentBlock, {"type": "text", "text": "BLOCKED"})]


async def _drop_all(_: list[BufferedToolCall]) -> list[AnthropicContentBlock]:
    return []


class TestBufferedToolCall:
    def test_input_parses_valid_json(self):
        tc = BufferedToolCall(id="t1", name="Bash", input_json='{"command": "ls"}')
        assert tc.input == {"command": "ls"}

    def test_input_empty_returns_empty_dict(self):
        assert BufferedToolCall(id="t1", name="Bash").input == {}

    def test_input_malformed_wraps_in_raw(self):
        tc = BufferedToolCall(id="t1", name="Bash", input_json='{"command":')
        assert tc.input == {"_raw": '{"command":'}

    def test_input_non_dict_wraps_in_raw(self):
        tc = BufferedToolCall(id="t1", name="Bash", input_json="[1, 2, 3]")
        assert tc.input == {"_raw": "[1, 2, 3]"}

    def test_as_content_block_shape(self):
        tc = BufferedToolCall(id="t1", name="Bash", input_json='{"command": "ls"}')
        assert tc.as_content_block() == {
            "type": "tool_use",
            "id": "t1",
            "name": "Bash",
            "input": {"command": "ls"},
        }


class TestStreamingPassthroughNoToolUse:
    @pytest.mark.asyncio
    async def test_text_only_response_passes_through_unchanged(self):
        buf = ToolCallStreamBuffer(_passthrough)
        events = []
        events += await buf.process(_text_start(0))
        events += await buf.process(_text_delta(0, "hello"))
        events += await buf.process(_block_stop(0))
        events += await buf.process(_message_delta("end_turn"))

        assert len(events) == 4
        assert isinstance(events[0], RawContentBlockStartEvent)
        assert events[0].index == 0
        assert isinstance(events[1], RawContentBlockDeltaEvent)
        assert events[1].index == 0
        assert isinstance(events[2], RawContentBlockStopEvent)
        assert events[2].index == 0
        assert isinstance(events[3], RawMessageDeltaEvent)
        assert events[3].delta.stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_transform_not_called_when_no_tool_use(self):
        called = False

        async def transform(calls):
            nonlocal called
            called = True
            return []

        buf = ToolCallStreamBuffer(transform)
        await buf.process(_text_start(0))
        await buf.process(_text_delta(0, "hi"))
        await buf.process(_block_stop(0))
        await buf.process(_message_delta("end_turn"))
        assert called is False


class TestStreamingToolUseBuffering:
    @pytest.mark.asyncio
    async def test_passthrough_transform_reconstructs_tool_use(self):
        buf = ToolCallStreamBuffer(_passthrough)
        await buf.process(_tool_start(0, "tu_1", "Bash"))
        await buf.process(_json_delta(0, '{"command":'))
        await buf.process(_json_delta(0, ' "ls"}'))
        await buf.process(_block_stop(0))
        emitted_after_delta = await buf.process(_message_delta("tool_use"))

        assert len(emitted_after_delta) == 4
        start, delta, stop, msg_delta = emitted_after_delta
        assert isinstance(start, RawContentBlockStartEvent)
        assert isinstance(start.content_block, ToolUseBlock)
        assert start.content_block.id == "tu_1"
        assert start.content_block.name == "Bash"
        assert start.index == 0
        assert isinstance(delta, RawContentBlockDeltaEvent)
        assert isinstance(delta.delta, InputJSONDelta)
        assert delta.delta.partial_json == '{"command": "ls"}'
        assert isinstance(stop, RawContentBlockStopEvent)
        assert stop.index == 0
        assert isinstance(msg_delta, RawMessageDeltaEvent)
        assert msg_delta.delta.stop_reason == "tool_use"

    @pytest.mark.asyncio
    async def test_tool_use_buffered_emits_nothing_until_message_delta(self):
        buf = ToolCallStreamBuffer(_passthrough)
        out_start = await buf.process(_tool_start(0, "tu_1", "Bash"))
        out_delta = await buf.process(_json_delta(0, '{"command": "ls"}'))
        out_stop = await buf.process(_block_stop(0))
        assert out_start == []
        assert out_delta == []
        assert out_stop == []


class TestStreamingStopReasonRewrite:
    @pytest.mark.asyncio
    async def test_block_all_rewrites_tool_use_to_end_turn(self):
        buf = ToolCallStreamBuffer(_block_all)
        await buf.process(_tool_start(0, "tu_1", "Bash"))
        await buf.process(_json_delta(0, '{"command": "rm -rf /"}'))
        await buf.process(_block_stop(0))
        events = await buf.process(_message_delta("tool_use"))

        msg_delta = events[-1]
        assert isinstance(msg_delta, RawMessageDeltaEvent)
        assert msg_delta.delta.stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_partial_block_preserves_tool_use_stop_reason(self):
        async def block_first_keep_rest(calls):
            return [
                cast(AnthropicContentBlock, {"type": "text", "text": "BLOCKED"}),
                calls[1].as_content_block(),
            ]

        buf = ToolCallStreamBuffer(block_first_keep_rest)
        await buf.process(_tool_start(0, "tu_1", "Bash"))
        await buf.process(_json_delta(0, '{"command": "rm -rf /"}'))
        await buf.process(_block_stop(0))
        await buf.process(_tool_start(1, "tu_2", "Read"))
        await buf.process(_json_delta(1, '{"path": "/etc/passwd"}'))
        await buf.process(_block_stop(1))
        events = await buf.process(_message_delta("tool_use"))

        msg_delta = events[-1]
        assert isinstance(msg_delta, RawMessageDeltaEvent)
        assert msg_delta.delta.stop_reason == "tool_use"

    @pytest.mark.asyncio
    async def test_non_tool_use_stop_reason_not_rewritten(self):
        buf = ToolCallStreamBuffer(_block_all)
        await buf.process(_tool_start(0, "tu_1", "Bash"))
        await buf.process(_json_delta(0, '{"command": "ls"}'))
        await buf.process(_block_stop(0))
        events = await buf.process(_message_delta("max_tokens"))

        msg_delta = events[-1]
        assert isinstance(msg_delta, RawMessageDeltaEvent)
        assert msg_delta.delta.stop_reason == "max_tokens"


class TestStreamingDropAllProducesNoBlocks:
    @pytest.mark.asyncio
    async def test_drop_all_emits_only_message_delta_with_end_turn(self):
        buf = ToolCallStreamBuffer(_drop_all)
        await buf.process(_tool_start(0, "tu_1", "Bash"))
        await buf.process(_json_delta(0, '{"command": "ls"}'))
        await buf.process(_block_stop(0))
        events = await buf.process(_message_delta("tool_use"))

        assert len(events) == 1
        assert isinstance(events[0], RawMessageDeltaEvent)
        assert events[0].delta.stop_reason == "end_turn"


class TestStreamingTextThenToolOutputIndices:
    @pytest.mark.asyncio
    async def test_text_streams_then_tool_replacement_uses_sequential_indices(self):
        buf = ToolCallStreamBuffer(_passthrough)

        events: list = []
        events += await buf.process(_text_start(0))
        events += await buf.process(_text_delta(0, "thinking..."))
        events += await buf.process(_block_stop(0))
        events += await buf.process(_tool_start(1, "tu_1", "Bash"))
        events += await buf.process(_json_delta(1, '{"command": "ls"}'))
        events += await buf.process(_block_stop(1))
        events += await buf.process(_message_delta("tool_use"))

        assert events[0].index == 0  # text start
        assert events[2].index == 0  # text stop
        assert events[3].index == 1  # reconstructed tool_use start
        assert events[5].index == 1  # tool_use stop


class TestStreamingMessageStartAndStopPassThrough:
    @pytest.mark.asyncio
    async def test_message_start_and_stop_pass_through(self):
        buf = ToolCallStreamBuffer(_passthrough)
        start = RawMessageStartEvent(
            type="message_start",
            message=RawMessage(
                id="msg_1",
                type="message",
                role="assistant",
                model="claude-haiku-4-5",
                content=[],
                stop_reason=None,
                stop_sequence=None,
                usage={"input_tokens": 1, "output_tokens": 0},  # type: ignore[arg-type]
            ),
        )
        stop = RawMessageStopEvent(type="message_stop")
        assert await buf.process(start) == [start]
        assert await buf.process(stop) == [stop]


class TestNonStreamingTransform:
    @pytest.mark.asyncio
    async def test_no_tool_use_returns_response_unchanged(self):
        response = cast(
            AnthropicResponse,
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "hello"}],
                "model": "claude-haiku-4-5",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )
        result = await transform_anthropic_response(response, _passthrough)
        assert result is response

    @pytest.mark.asyncio
    async def test_block_all_rewrites_stop_reason(self):
        response = cast(
            AnthropicResponse,
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "ok"},
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
                ],
                "model": "claude-haiku-4-5",
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )
        result = await transform_anthropic_response(response, _block_all)
        assert result.get("stop_reason") == "end_turn"
        assert result["content"] == [
            {"type": "text", "text": "ok"},
            {"type": "text", "text": "BLOCKED"},
        ]

    @pytest.mark.asyncio
    async def test_passthrough_preserves_content_and_stop_reason(self):
        response = cast(
            AnthropicResponse,
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
                ],
                "model": "claude-haiku-4-5",
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )
        result = await transform_anthropic_response(response, _passthrough)
        assert result.get("stop_reason") == "tool_use"
        assert result["content"] == [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
        ]

    @pytest.mark.asyncio
    async def test_interleaved_text_and_tool_preserves_positions_when_count_matches(self):
        """1-to-1 transform output preserves original tool_use positions in non-streaming."""
        response = cast(
            AnthropicResponse,
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "first"},
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
                    {"type": "text", "text": "middle"},
                    {"type": "tool_use", "id": "t2", "name": "Read", "input": {"path": "/x"}},
                    {"type": "text", "text": "last"},
                ],
                "model": "claude-haiku-4-5",
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

        async def block_each(calls):
            return [cast(AnthropicContentBlock, {"type": "text", "text": f"BLOCKED {c.name}"}) for c in calls]

        result = await transform_anthropic_response(response, block_each)
        assert result["content"] == [
            {"type": "text", "text": "first"},
            {"type": "text", "text": "BLOCKED Bash"},
            {"type": "text", "text": "middle"},
            {"type": "text", "text": "BLOCKED Read"},
            {"type": "text", "text": "last"},
        ]
        assert result.get("stop_reason") == "end_turn"

    @pytest.mark.asyncio
    async def test_identity_fast_path_returns_original_object_for_passthrough(self):
        """Pure-passthrough transform with unchanged stop_reason returns the same response object."""
        response = cast(
            AnthropicResponse,
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
                ],
                "model": "claude-haiku-4-5",
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )
        result = await transform_anthropic_response(response, _passthrough)
        assert result is response

    @pytest.mark.asyncio
    async def test_multiple_tool_use_blocks_replaced_with_single_output_at_first_position(self):
        response = cast(
            AnthropicResponse,
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "doing two things"},
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
                    {"type": "tool_use", "id": "t2", "name": "Read", "input": {"path": "/x"}},
                ],
                "model": "claude-haiku-4-5",
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )
        result = await transform_anthropic_response(response, _block_all)
        assert result["content"] == [
            {"type": "text", "text": "doing two things"},
            {"type": "text", "text": "BLOCKED"},
        ]
        assert result.get("stop_reason") == "end_turn"


class TestMalformedInputPassthroughPreservesRawBytes:
    @pytest.mark.asyncio
    async def test_malformed_json_passthrough_emits_original_bytes(self):
        """Passthrough of a tool_use with malformed input_json must re-emit the
        original (broken) bytes verbatim, not a {"_raw": ...} JSON normalization
        the downstream client could erroneously accept and execute.
        """
        buf = ToolCallStreamBuffer(_passthrough)
        await buf.process(_tool_start(0, "tu_1", "Bash"))
        await buf.process(_json_delta(0, '{"command":"ls"'))  # missing closing brace
        await buf.process(_block_stop(0))
        emitted = await buf.process(_message_delta("tool_use"))

        delta = emitted[1]
        assert isinstance(delta, RawContentBlockDeltaEvent)
        assert isinstance(delta.delta, InputJSONDelta)
        assert delta.delta.partial_json == '{"command":"ls"'


class TestUnknownUpstreamIndexDropped:
    @pytest.mark.asyncio
    async def test_delta_for_unseen_index_dropped(self):
        """A delta for an index we never saw a block_start for must not pass
        through with its raw upstream index (could collide with output indices).
        """
        buf = ToolCallStreamBuffer(_passthrough)
        # Never sent a start event for index 5.
        result = await buf.process(_text_delta(5, "stray"))
        assert result == []

    @pytest.mark.asyncio
    async def test_stop_for_unseen_index_dropped(self):
        buf = ToolCallStreamBuffer(_passthrough)
        result = await buf.process(_block_stop(5))
        assert result == []


class TestUnsupportedBlockType:
    @pytest.mark.asyncio
    async def test_invalid_block_type_in_transform_output_raises(self):
        async def bad_transform(_):
            return [cast(AnthropicContentBlock, {"type": "image", "source": {}})]

        buf = ToolCallStreamBuffer(bad_transform)
        await buf.process(_tool_start(0, "tu_1", "Bash"))
        await buf.process(_block_stop(0))
        with pytest.raises(ValueError, match="unsupported block type"):
            await buf.process(_message_delta("tool_use"))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
