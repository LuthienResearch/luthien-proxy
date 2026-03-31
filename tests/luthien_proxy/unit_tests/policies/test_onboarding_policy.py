"""Unit tests for OnboardingPolicy."""

from __future__ import annotations

import pytest
from anthropic.types import (
    InputJSONDelta,
    MessageDeltaUsage,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    RawMessageStopEvent,
    TextBlock,
    TextDelta,
    ToolUseBlock,
)
from anthropic.types.raw_message_delta_event import Delta

from luthien_proxy.policies.onboarding_policy import (
    OnboardingPolicy,
    OnboardingPolicyConfig,
    is_first_turn,
)
from luthien_proxy.policy_core import (
    BasePolicy,
    TextModifierPolicy,
)
from luthien_proxy.policy_core.policy_context import PolicyContext


@pytest.fixture
def policy():
    return OnboardingPolicy({"gateway_url": "http://localhost:9999"})


@pytest.fixture
def context():
    return PolicyContext.for_testing()


# =============================================================================
# is_first_turn tests
# =============================================================================


class TestIsFirstTurn:
    def test_single_user_message(self):
        request = {"messages": [{"role": "user", "content": "hello"}]}
        assert is_first_turn(request) is True

    def test_conversation_with_assistant_response(self):
        request = {
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
                {"role": "user", "content": "how are you"},
            ]
        }
        assert is_first_turn(request) is False

    def test_empty_messages(self):
        assert is_first_turn({"messages": []}) is False

    def test_no_messages_key(self):
        assert is_first_turn({}) is False

    def test_system_message_with_single_user(self):
        """System messages don't count as user or assistant turns."""
        request = {
            "messages": [
                {"role": "user", "content": "hello"},
            ]
        }
        assert is_first_turn(request) is True

    def test_multiple_user_messages_no_assistant(self):
        """Edge case: multiple user messages but no assistant — not first turn."""
        request = {
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "user", "content": "are you there?"},
            ]
        }
        assert is_first_turn(request) is False


# =============================================================================
# Protocol compliance
# =============================================================================


class TestProtocol:
    def test_inherits_text_modifier(self, policy):
        assert isinstance(policy, TextModifierPolicy)

    def test_inherits_base_policy(self, policy):
        assert isinstance(policy, BasePolicy)


# =============================================================================
# Config
# =============================================================================


class TestConfig:
    def test_default_gateway_url(self):
        policy = OnboardingPolicy()
        assert policy._gateway_url == "http://localhost:8000"

    def test_custom_gateway_url(self, policy):
        assert policy._gateway_url == "http://localhost:9999"

    def test_trailing_slash_stripped(self):
        policy = OnboardingPolicy({"gateway_url": "http://localhost:8000/"})
        assert policy._gateway_url == "http://localhost:8000"

    def test_config_from_pydantic(self):
        config = OnboardingPolicyConfig(gateway_url="http://example.com")
        policy = OnboardingPolicy(config)
        assert policy._gateway_url == "http://example.com"


# =============================================================================
# Welcome message content
# =============================================================================


class TestWelcomeMessage:
    def test_contains_config_url(self, policy):
        assert "http://localhost:9999/policy-config" in policy._welcome

    def test_contains_luthien_branding(self, policy):
        assert "Luthien" in policy._welcome

    def test_extra_text_returns_welcome(self, policy):
        assert policy.extra_text() == policy._welcome


# =============================================================================
# Non-streaming response (via hook interface for MultiSerialPolicy)
# =============================================================================


class TestNonStreamingResponse:
    @pytest.mark.asyncio
    async def test_first_turn_appends_welcome(self, policy, context):
        """On first turn, welcome text is appended to the last text block."""
        request = {"messages": [{"role": "user", "content": "hi"}]}
        await policy.on_anthropic_request(request, context)
        response = {
            "content": [{"type": "text", "text": "Hello!"}],
            "model": "test",
            "role": "assistant",
        }
        result = await policy.on_anthropic_response(response, context)
        content_blocks = result["content"]
        assert len(content_blocks) == 1
        assert content_blocks[0]["text"].startswith("Hello!")
        assert "Luthien" in content_blocks[0]["text"]

    @pytest.mark.asyncio
    async def test_subsequent_turn_passthrough(self, policy, context):
        """On subsequent turns, response passes through unchanged."""
        request = {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "how are you"},
            ]
        }
        await policy.on_anthropic_request(request, context)
        response = {
            "content": [{"type": "text", "text": "I'm fine!"}],
            "model": "test",
            "role": "assistant",
        }
        result = await policy.on_anthropic_response(response, context)
        assert len(result["content"]) == 1
        assert result["content"][0]["text"] == "I'm fine!"

    @pytest.mark.asyncio
    async def test_first_turn_works_without_context_request(self, policy, context):
        """Hook methods work when context.request is None (the MultiSerialPolicy bug)."""
        assert context.request is None
        request = {"messages": [{"role": "user", "content": "hi"}]}
        await policy.on_anthropic_request(request, context)
        response = {
            "content": [{"type": "text", "text": "Hello!"}],
            "model": "test",
            "role": "assistant",
        }
        result = await policy.on_anthropic_response(response, context)
        assert len(result["content"]) == 1
        assert result["content"][0]["text"].startswith("Hello!")
        assert "Luthien" in result["content"][0]["text"]


# =============================================================================
# Streaming (via hook interface)
# =============================================================================


class TestStreamingHooks:
    @pytest.mark.asyncio
    async def test_suffix_flushed_before_message_delta(self, policy, context):
        """Suffix + held stop are flushed when message_delta arrives, not in stream_complete."""
        request = {"messages": [{"role": "user", "content": "hi"}]}
        await policy.on_anthropic_request(request, context)

        start_event = RawContentBlockStartEvent(
            type="content_block_start",
            index=0,
            content_block=TextBlock(type="text", text=""),
        )
        stop_event = RawContentBlockStopEvent(type="content_block_stop", index=0)
        message_delta = RawMessageDeltaEvent(
            type="message_delta",
            delta=Delta(stop_reason="end_turn", stop_sequence=None),
            usage=MessageDeltaUsage(output_tokens=20),
        )

        await policy.on_anthropic_stream_event(start_event, context)
        await policy.on_anthropic_stream_event(stop_event, context)

        # message_delta should trigger suffix flush
        events = await policy.on_anthropic_stream_event(message_delta, context)
        # Should emit: suffix text_delta, held content_block_stop, message_delta
        assert len(events) == 3
        assert isinstance(events[0], RawContentBlockDeltaEvent)
        assert isinstance(events[0].delta, TextDelta)
        assert "Luthien" in events[0].delta.text
        assert events[0].index == 0
        assert isinstance(events[1], RawContentBlockStopEvent)
        assert events[1].index == 0
        assert isinstance(events[2], RawMessageDeltaEvent)

        # stream_complete should have nothing left
        complete_events = await policy.on_anthropic_stream_complete(context)
        assert complete_events == []

    @pytest.mark.asyncio
    async def test_stream_complete_fallback_without_message_delta(self, policy, context):
        """If stream ends without message_delta, stream_complete still flushes."""
        request = {"messages": [{"role": "user", "content": "hi"}]}
        await policy.on_anthropic_request(request, context)

        start_event = RawContentBlockStartEvent(
            type="content_block_start",
            index=0,
            content_block=TextBlock(type="text", text=""),
        )
        stop_event = RawContentBlockStopEvent(type="content_block_stop", index=0)

        await policy.on_anthropic_stream_event(start_event, context)
        await policy.on_anthropic_stream_event(stop_event, context)

        # No message_delta — stream_complete should still flush
        events = await policy.on_anthropic_stream_complete(context)
        assert len(events) == 2
        assert isinstance(events[0], RawContentBlockDeltaEvent)
        assert "Luthien" in events[0].delta.text
        assert isinstance(events[1], RawContentBlockStopEvent)

    @pytest.mark.asyncio
    async def test_stream_complete_empty_on_subsequent_turn(self, policy, context):
        """on_anthropic_stream_complete returns empty on subsequent turns."""
        request = {
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "more"},
            ]
        }
        await policy.on_anthropic_request(request, context)
        events = await policy.on_anthropic_stream_complete(context)
        assert events == []

    @pytest.mark.asyncio
    async def test_hooks_inert_without_on_anthropic_request(self, policy, context):
        """If on_anthropic_request was never called, hooks are inert (no crash)."""
        assert context.request is None
        response = {
            "content": [{"type": "text", "text": "Hello!"}],
            "model": "test",
            "role": "assistant",
        }
        result = await policy.on_anthropic_response(response, context)
        assert result["content"][0]["text"] == "Hello!"
        assert len(result["content"]) == 1

        events = await policy.on_anthropic_stream_complete(context)
        assert events == []


# =============================================================================
# Tool use interleaving — extra_text must not follow tool_use blocks
# =============================================================================


class TestToolUseInterleaving:
    """Verify that extra_text is appended to the last text block, never after tool_use.

    The Anthropic API requires text blocks to precede tool_use blocks in
    assistant messages. Appending text after tool_use causes a 400 error
    on the next turn.
    """

    @pytest.mark.asyncio
    async def test_non_streaming_appends_to_last_text_block(self, policy, context):
        """Welcome text is appended to the last text block, not added as a new block."""
        request = {"messages": [{"role": "user", "content": "hi"}]}
        await policy.on_anthropic_request(request, context)
        response = {
            "content": [
                {"type": "text", "text": "Let me check."},
                {"type": "tool_use", "id": "tool_1", "name": "Read", "input": {"path": "/tmp"}},
            ],
            "model": "test",
            "role": "assistant",
        }
        result = await policy.on_anthropic_response(response, context)
        content = result["content"]
        # Same number of blocks — suffix appended to existing text block
        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[0]["text"].startswith("Let me check.")
        assert "Luthien" in content[0]["text"]
        assert content[1]["type"] == "tool_use"

    @pytest.mark.asyncio
    async def test_streaming_injects_suffix_before_tool_use(self, policy, context):
        """In streaming, suffix delta is injected into the text block before tool_use starts."""
        request = {"messages": [{"role": "user", "content": "hi"}]}
        await policy.on_anthropic_request(request, context)

        events_in = [
            RawContentBlockStartEvent(
                type="content_block_start",
                index=0,
                content_block=TextBlock(type="text", text=""),
            ),
            RawContentBlockDeltaEvent(
                type="content_block_delta",
                index=0,
                delta=TextDelta(type="text_delta", text="Let me check."),
            ),
            RawContentBlockStopEvent(type="content_block_stop", index=0),
            RawContentBlockStartEvent(
                type="content_block_start",
                index=1,
                content_block=ToolUseBlock(type="tool_use", id="tool_1", name="Read", input={}),
            ),
            RawContentBlockDeltaEvent(
                type="content_block_delta",
                index=1,
                delta=InputJSONDelta(type="input_json_delta", partial_json='{"path":"/tmp"}'),
            ),
            RawContentBlockStopEvent(type="content_block_stop", index=1),
        ]

        all_events_out = []
        for event in events_in:
            result = await policy.on_anthropic_stream_event(event, context)
            all_events_out.extend(result)

        complete_events = await policy.on_anthropic_stream_complete(context)
        all_events_out.extend(complete_events)

        # No new content blocks — still just 2 (text + tool_use)
        starts = [e for e in all_events_out if isinstance(e, RawContentBlockStartEvent)]
        assert len(starts) == 2

        # Suffix was injected as a delta in the text block (index 0)
        welcome_deltas = [
            e
            for e in all_events_out
            if isinstance(e, RawContentBlockDeltaEvent) and isinstance(e.delta, TextDelta) and "Luthien" in e.delta.text
        ]
        assert len(welcome_deltas) == 1
        assert welcome_deltas[0].index == 0

        # The suffix delta appears before the tool_use start
        suffix_pos = all_events_out.index(welcome_deltas[0])
        tool_start_pos = all_events_out.index(starts[1])
        assert suffix_pos < tool_start_pos


# =============================================================================
# Streaming protocol ordering — content blocks must precede message_delta
# =============================================================================


class TestStreamingProtocolOrdering:
    """Verify that injected content blocks always appear before message_delta.

    Reproduces the bug from onboarding dogfood findings (March 26):
    OnboardingPolicy was emitting content blocks in on_anthropic_stream_complete,
    which fires AFTER message_delta and message_stop have already been sent.
    """

    @pytest.mark.asyncio
    async def test_full_stream_text_only_protocol_order(self, policy, context):
        """Text-only response: suffix + held stop appear before message_delta."""
        request = {"messages": [{"role": "user", "content": "hi"}]}
        await policy.on_anthropic_request(request, context)

        events_in = [
            RawContentBlockStartEvent(
                type="content_block_start",
                index=0,
                content_block=TextBlock(type="text", text=""),
            ),
            RawContentBlockDeltaEvent(
                type="content_block_delta",
                index=0,
                delta=TextDelta(type="text_delta", text="Hello!"),
            ),
            RawContentBlockStopEvent(type="content_block_stop", index=0),
            RawMessageDeltaEvent(
                type="message_delta",
                delta=Delta(stop_reason="end_turn", stop_sequence=None),
                usage=MessageDeltaUsage(output_tokens=5),
            ),
            RawMessageStopEvent(type="message_stop"),
        ]

        all_out = []
        for event in events_in:
            result = await policy.on_anthropic_stream_event(event, context)
            all_out.extend(result)
        complete = await policy.on_anthropic_stream_complete(context)
        all_out.extend(complete)

        # Find positions
        message_delta_events = [e for e in all_out if isinstance(e, RawMessageDeltaEvent)]
        content_block_events = [
            e
            for e in all_out
            if isinstance(e, (RawContentBlockStartEvent, RawContentBlockDeltaEvent, RawContentBlockStopEvent))
        ]
        assert len(message_delta_events) == 1

        # ALL content block events must precede message_delta
        md_pos = all_out.index(message_delta_events[0])
        for cb_event in content_block_events:
            cb_pos = all_out.index(cb_event)
            assert cb_pos < md_pos, (
                f"{type(cb_event).__name__} at position {cb_pos} appeared after message_delta at position {md_pos}"
            )

        # stream_complete should have nothing left
        assert complete == []

    @pytest.mark.asyncio
    async def test_full_stream_with_tool_use_protocol_order(self, policy, context):
        """Text + tool_use response: suffix injected before tool_use, all before message_delta."""
        request = {"messages": [{"role": "user", "content": "hi"}]}
        await policy.on_anthropic_request(request, context)

        events_in = [
            RawContentBlockStartEvent(
                type="content_block_start",
                index=0,
                content_block=TextBlock(type="text", text=""),
            ),
            RawContentBlockDeltaEvent(
                type="content_block_delta",
                index=0,
                delta=TextDelta(type="text_delta", text="Let me check."),
            ),
            RawContentBlockStopEvent(type="content_block_stop", index=0),
            RawContentBlockStartEvent(
                type="content_block_start",
                index=1,
                content_block=ToolUseBlock(type="tool_use", id="t1", name="Read", input={}),
            ),
            RawContentBlockDeltaEvent(
                type="content_block_delta",
                index=1,
                delta=InputJSONDelta(type="input_json_delta", partial_json="{}"),
            ),
            RawContentBlockStopEvent(type="content_block_stop", index=1),
            RawMessageDeltaEvent(
                type="message_delta",
                delta=Delta(stop_reason="tool_use", stop_sequence=None),
                usage=MessageDeltaUsage(output_tokens=5),
            ),
            RawMessageStopEvent(type="message_stop"),
        ]

        all_out = []
        for event in events_in:
            result = await policy.on_anthropic_stream_event(event, context)
            all_out.extend(result)
        complete = await policy.on_anthropic_stream_complete(context)
        all_out.extend(complete)

        # ALL content block events must precede message_delta
        message_delta_events = [e for e in all_out if isinstance(e, RawMessageDeltaEvent)]
        content_block_events = [
            e
            for e in all_out
            if isinstance(e, (RawContentBlockStartEvent, RawContentBlockDeltaEvent, RawContentBlockStopEvent))
        ]
        md_pos = all_out.index(message_delta_events[0])
        for cb_event in content_block_events:
            cb_pos = all_out.index(cb_event)
            assert cb_pos < md_pos
