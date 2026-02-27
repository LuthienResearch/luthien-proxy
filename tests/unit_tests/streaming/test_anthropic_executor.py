# ABOUTME: Tests for AnthropicStreamExecutor that processes Anthropic streaming events through policy hooks
# ABOUTME: Verifies passthrough, filtering, transformation, and error handling behaviors

"""Tests for AnthropicStreamExecutor.

Verifies that the executor correctly:
- Passes events through with NoOp-like policies
- Filters events when policy returns []
- Transforms events when policy returns modified events
- Emits multiple events when policy returns a multi-element list
- Propagates policy errors to the caller
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from anthropic.types import (
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    RawMessageStartEvent,
    RawMessageStopEvent,
    TextDelta,
)

from conftest import DEFAULT_TEST_MODEL
from luthien_proxy.policy_core.anthropic_interface import (
    AnthropicPolicyInterface,
    AnthropicStreamEvent,
)
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.streaming.anthropic_executor import AnthropicStreamExecutor

# =============================================================================
# Test Fixtures and Helpers
# =============================================================================


async def async_iter_from_list(items: list[AnthropicStreamEvent]) -> AsyncIterator[AnthropicStreamEvent]:
    """Convert a list to an async iterator."""
    for item in items:
        yield item


def make_message_start_event(message_id: str = "msg_test") -> RawMessageStartEvent:
    """Create a message_start event for testing."""
    return RawMessageStartEvent.model_construct(
        type="message_start",
        message={
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": None,
            "usage": {"input_tokens": 10, "output_tokens": 0},
        },
    )


def make_content_block_start_event(index: int = 0) -> RawContentBlockStartEvent:
    """Create a content_block_start event for testing."""
    return RawContentBlockStartEvent.model_construct(
        type="content_block_start",
        index=index,
        content_block={"type": "text", "text": ""},
    )


def make_text_delta_event(text: str, index: int = 0) -> RawContentBlockDeltaEvent:
    """Create a content_block_delta event with text for testing."""
    return RawContentBlockDeltaEvent.model_construct(
        type="content_block_delta",
        index=index,
        delta=TextDelta.model_construct(type="text_delta", text=text),
    )


def make_content_block_stop_event(index: int = 0) -> RawContentBlockStopEvent:
    """Create a content_block_stop event for testing."""
    return RawContentBlockStopEvent.model_construct(type="content_block_stop", index=index)


def make_message_delta_event(stop_reason: str = "end_turn") -> RawMessageDeltaEvent:
    """Create a message_delta event for testing."""
    return RawMessageDeltaEvent.model_construct(
        type="message_delta",
        delta={"stop_reason": stop_reason, "stop_sequence": None},
        usage={"output_tokens": 10},
    )


def make_message_stop_event() -> RawMessageStopEvent:
    """Create a message_stop event for testing."""
    return RawMessageStopEvent.model_construct(type="message_stop")


@pytest.fixture
def policy_ctx() -> PolicyContext:
    """Create a PolicyContext for testing."""
    return PolicyContext.for_testing()


# =============================================================================
# Mock Policies for Testing
# =============================================================================


class PassthroughPolicy(AnthropicPolicyInterface):
    """Policy that passes all events through unchanged."""

    @property
    def short_policy_name(self) -> str:
        return "Passthrough"

    async def on_anthropic_request(self, request: Any, context: PolicyContext) -> Any:
        return request

    async def on_anthropic_response(self, response: Any, context: PolicyContext) -> Any:
        return response

    async def on_anthropic_stream_event(
        self, event: AnthropicStreamEvent, context: PolicyContext
    ) -> list[AnthropicStreamEvent]:
        return [event]


class FilteringPolicy(AnthropicPolicyInterface):
    """Policy that filters out events containing specific text."""

    def __init__(self, filter_text: str = "secret"):
        self.filter_text = filter_text

    @property
    def short_policy_name(self) -> str:
        return "Filtering"

    async def on_anthropic_request(self, request: Any, context: PolicyContext) -> Any:
        return request

    async def on_anthropic_response(self, response: Any, context: PolicyContext) -> Any:
        return response

    async def on_anthropic_stream_event(
        self, event: AnthropicStreamEvent, context: PolicyContext
    ) -> list[AnthropicStreamEvent]:
        if isinstance(event, RawContentBlockDeltaEvent):
            if isinstance(event.delta, TextDelta):
                text = event.delta.text
                if self.filter_text in text.lower():
                    return []
        return [event]


class TransformingPolicy(AnthropicPolicyInterface):
    """Policy that transforms text deltas to uppercase."""

    @property
    def short_policy_name(self) -> str:
        return "Transforming"

    async def on_anthropic_request(self, request: Any, context: PolicyContext) -> Any:
        return request

    async def on_anthropic_response(self, response: Any, context: PolicyContext) -> Any:
        return response

    async def on_anthropic_stream_event(
        self, event: AnthropicStreamEvent, context: PolicyContext
    ) -> list[AnthropicStreamEvent]:
        if isinstance(event, RawContentBlockDeltaEvent):
            if isinstance(event.delta, TextDelta):
                event.delta.text = event.delta.text.upper()
        return [event]


class ErrorThrowingPolicy(AnthropicPolicyInterface):
    """Policy that throws an error on specific events."""

    def __init__(self, error_on_text: str = "error"):
        self.error_on_text = error_on_text

    @property
    def short_policy_name(self) -> str:
        return "ErrorThrowing"

    async def on_anthropic_request(self, request: Any, context: PolicyContext) -> Any:
        return request

    async def on_anthropic_response(self, response: Any, context: PolicyContext) -> Any:
        return response

    async def on_anthropic_stream_event(
        self, event: AnthropicStreamEvent, context: PolicyContext
    ) -> list[AnthropicStreamEvent]:
        if isinstance(event, RawContentBlockDeltaEvent):
            if isinstance(event.delta, TextDelta):
                text = event.delta.text
                if self.error_on_text in text.lower():
                    raise ValueError(f"Policy error on text: {text}")
        return [event]


# =============================================================================
# Tests for Basic Passthrough
# =============================================================================


class TestAnthropicStreamExecutorPassthrough:
    """Tests for basic passthrough behavior."""

    @pytest.mark.asyncio
    async def test_single_event_passthrough(self, policy_ctx: PolicyContext):
        """Test that a single event passes through unchanged."""
        executor = AnthropicStreamExecutor()
        policy = PassthroughPolicy()

        event = make_text_delta_event("Hello")
        stream = async_iter_from_list([event])

        results = []
        async for result in executor.process(stream, policy, policy_ctx):
            results.append(result)

        assert len(results) == 1
        assert results[0] == event

    @pytest.mark.asyncio
    async def test_multiple_events_passthrough(self, policy_ctx: PolicyContext):
        """Test that multiple events pass through in order."""
        executor = AnthropicStreamExecutor()
        policy = PassthroughPolicy()

        events: list[AnthropicStreamEvent] = [
            make_message_start_event(),
            make_content_block_start_event(),
            make_text_delta_event("Hello"),
            make_text_delta_event(" world"),
            make_content_block_stop_event(),
            make_message_delta_event(),
            make_message_stop_event(),
        ]
        stream = async_iter_from_list(events)

        results = []
        async for result in executor.process(stream, policy, policy_ctx):
            results.append(result)

        assert len(results) == len(events)
        for i, result in enumerate(results):
            assert result == events[i]

    @pytest.mark.asyncio
    async def test_empty_stream_produces_no_output(self, policy_ctx: PolicyContext):
        """Test that empty stream produces no output."""
        executor = AnthropicStreamExecutor()
        policy = PassthroughPolicy()

        stream = async_iter_from_list([])

        results = []
        async for result in executor.process(stream, policy, policy_ctx):
            results.append(result)

        assert len(results) == 0


# =============================================================================
# Tests for Event Filtering
# =============================================================================


class TestAnthropicStreamExecutorFiltering:
    """Tests for event filtering behavior."""

    @pytest.mark.asyncio
    async def test_filter_specific_events(self, policy_ctx: PolicyContext):
        """Test that events can be filtered out by returning None."""
        executor = AnthropicStreamExecutor()
        policy = FilteringPolicy(filter_text="secret")

        events: list[AnthropicStreamEvent] = [
            make_text_delta_event("Hello"),
            make_text_delta_event("This is secret"),
            make_text_delta_event("World"),
        ]
        stream = async_iter_from_list(events)

        results = []
        async for result in executor.process(stream, policy, policy_ctx):
            results.append(result)

        assert len(results) == 2
        delta1 = results[0]
        delta2 = results[1]
        assert isinstance(delta1, RawContentBlockDeltaEvent)
        assert isinstance(delta2, RawContentBlockDeltaEvent)
        assert isinstance(delta1.delta, TextDelta)
        assert isinstance(delta2.delta, TextDelta)
        assert delta1.delta.text == "Hello"
        assert delta2.delta.text == "World"

    @pytest.mark.asyncio
    async def test_filter_all_events(self, policy_ctx: PolicyContext):
        """Test that all events can be filtered out."""
        executor = AnthropicStreamExecutor()
        policy = FilteringPolicy(filter_text="x")  # matches all

        events: list[AnthropicStreamEvent] = [
            make_text_delta_event("text x"),
            make_text_delta_event("x text"),
        ]
        stream = async_iter_from_list(events)

        results = []
        async for result in executor.process(stream, policy, policy_ctx):
            results.append(result)

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_non_text_events_pass_through_filtering_policy(self, policy_ctx: PolicyContext):
        """Test that non-text events pass through a text-filtering policy."""
        executor = AnthropicStreamExecutor()
        policy = FilteringPolicy(filter_text="secret")

        events: list[AnthropicStreamEvent] = [
            make_message_start_event(),
            make_content_block_start_event(),
            make_content_block_stop_event(),
            make_message_stop_event(),
        ]
        stream = async_iter_from_list(events)

        results = []
        async for result in executor.process(stream, policy, policy_ctx):
            results.append(result)

        assert len(results) == 4


# =============================================================================
# Tests for Event Transformation
# =============================================================================


class TestAnthropicStreamExecutorTransformation:
    """Tests for event transformation behavior."""

    @pytest.mark.asyncio
    async def test_transform_text_delta(self, policy_ctx: PolicyContext):
        """Test that events can be transformed by the policy."""
        executor = AnthropicStreamExecutor()
        policy = TransformingPolicy()

        events: list[AnthropicStreamEvent] = [make_text_delta_event("hello")]
        stream = async_iter_from_list(events)

        results = []
        async for result in executor.process(stream, policy, policy_ctx):
            results.append(result)

        assert len(results) == 1
        delta = results[0]
        assert isinstance(delta, RawContentBlockDeltaEvent)
        assert isinstance(delta.delta, TextDelta)
        assert delta.delta.text == "HELLO"

    @pytest.mark.asyncio
    async def test_transform_preserves_non_text_events(self, policy_ctx: PolicyContext):
        """Test that non-text events pass through unchanged by transforming policy."""
        executor = AnthropicStreamExecutor()
        policy = TransformingPolicy()

        events: list[AnthropicStreamEvent] = [
            make_message_start_event("msg_123"),
            make_content_block_start_event(),
            make_text_delta_event("hello"),
            make_content_block_stop_event(),
            make_message_stop_event(),
        ]
        stream = async_iter_from_list(events)

        results = []
        async for result in executor.process(stream, policy, policy_ctx):
            results.append(result)

        assert len(results) == 5
        # Check message_start is unchanged
        msg_start = results[0]
        assert isinstance(msg_start, RawMessageStartEvent)
        assert msg_start.message.id == "msg_123"
        # Check text delta is transformed
        delta = results[2]
        assert isinstance(delta, RawContentBlockDeltaEvent)
        assert isinstance(delta.delta, TextDelta)
        assert delta.delta.text == "HELLO"


# =============================================================================
# Tests for Error Propagation
# =============================================================================


class TestAnthropicStreamExecutorErrorPropagation:
    """Tests for error propagation behavior.

    Policy errors propagate to the caller - if something fails, it fails loudly.
    """

    @pytest.mark.asyncio
    async def test_policy_error_propagates(self, policy_ctx: PolicyContext):
        """Test that errors in policy propagate to caller."""
        executor = AnthropicStreamExecutor()
        policy = ErrorThrowingPolicy(error_on_text="error")

        events: list[AnthropicStreamEvent] = [
            make_text_delta_event("Hello"),
            make_text_delta_event("This triggers error"),
            make_text_delta_event("World"),
        ]
        stream = async_iter_from_list(events)

        results = []
        with pytest.raises(ValueError, match="Policy error on text"):
            async for result in executor.process(stream, policy, policy_ctx):
                results.append(result)

        # Only the first event should have been processed before the error
        assert len(results) == 1
        delta = results[0]
        assert isinstance(delta, RawContentBlockDeltaEvent)
        assert isinstance(delta.delta, TextDelta)
        assert delta.delta.text == "Hello"

    @pytest.mark.asyncio
    async def test_policy_error_on_first_event_propagates(self, policy_ctx: PolicyContext):
        """Test that error on first event propagates immediately."""
        executor = AnthropicStreamExecutor()
        policy = ErrorThrowingPolicy(error_on_text="error")

        events: list[AnthropicStreamEvent] = [
            make_text_delta_event("error first"),
            make_text_delta_event("second"),
        ]
        stream = async_iter_from_list(events)

        results = []
        with pytest.raises(ValueError, match="Policy error on text"):
            async for result in executor.process(stream, policy, policy_ctx):
                results.append(result)

        # No events should have been processed
        assert len(results) == 0


# =============================================================================
# Tests for Protocol Compliance
# =============================================================================


class TestAnthropicStreamExecutorProtocolCompliance:
    """Tests verifying correct interaction with AnthropicPolicyProtocol."""

    @pytest.mark.asyncio
    async def test_policy_receives_context(self, policy_ctx: PolicyContext):
        """Test that policy receives the correct context."""

        class ContextTrackingPolicy:
            def __init__(self):
                self.received_contexts: list[PolicyContext] = []

            @property
            def short_policy_name(self) -> str:
                return "ContextTracking"

            async def on_anthropic_request(self, request: Any, context: PolicyContext) -> Any:
                return request

            async def on_anthropic_response(self, response: Any, context: PolicyContext) -> Any:
                return response

            async def on_anthropic_stream_event(
                self, event: AnthropicStreamEvent, context: PolicyContext
            ) -> list[AnthropicStreamEvent]:
                self.received_contexts.append(context)
                return [event]

        executor = AnthropicStreamExecutor()
        policy = ContextTrackingPolicy()

        events: list[AnthropicStreamEvent] = [make_text_delta_event("test")]
        stream = async_iter_from_list(events)

        async for _ in executor.process(stream, policy, policy_ctx):
            pass

        assert len(policy.received_contexts) == 1
        assert policy.received_contexts[0] is policy_ctx

    def test_passthrough_policy_has_required_methods(self):
        """Test that our test policies have the required methods."""
        policy = PassthroughPolicy()
        assert hasattr(policy, "on_anthropic_stream_event")
        assert hasattr(policy, "on_anthropic_request")
        assert hasattr(policy, "on_anthropic_response")

    def test_filtering_policy_has_required_methods(self):
        """Test that filtering policy has the required methods."""
        policy = FilteringPolicy()
        assert hasattr(policy, "on_anthropic_stream_event")
        assert hasattr(policy, "on_anthropic_request")
        assert hasattr(policy, "on_anthropic_response")

    def test_transforming_policy_has_required_methods(self):
        """Test that transforming policy has the required methods."""
        policy = TransformingPolicy()
        assert hasattr(policy, "on_anthropic_stream_event")
        assert hasattr(policy, "on_anthropic_request")
        assert hasattr(policy, "on_anthropic_response")


# =============================================================================
# Tests for Multi-Event Returns
# =============================================================================


class MultiEventPolicy(AnthropicPolicyInterface):
    """Policy that returns multiple events for a single input event."""

    @property
    def short_policy_name(self) -> str:
        return "MultiEvent"

    async def on_anthropic_request(self, request: Any, context: PolicyContext) -> Any:
        return request

    async def on_anthropic_response(self, response: Any, context: PolicyContext) -> Any:
        return response

    async def on_anthropic_stream_event(
        self, event: AnthropicStreamEvent, context: PolicyContext
    ) -> list[AnthropicStreamEvent]:
        """Expand each text delta into three events: original + two extras."""
        if isinstance(event, RawContentBlockDeltaEvent) and isinstance(event.delta, TextDelta):
            extra1 = make_text_delta_event(f"[extra1:{event.delta.text}]", index=event.index)
            extra2 = make_text_delta_event(f"[extra2:{event.delta.text}]", index=event.index)
            return [event, extra1, extra2]
        return [event]


class SelectiveMultiEventPolicy(AnthropicPolicyInterface):
    """Policy that returns varying numbers of events per input."""

    @property
    def short_policy_name(self) -> str:
        return "SelectiveMultiEvent"

    async def on_anthropic_request(self, request: Any, context: PolicyContext) -> Any:
        return request

    async def on_anthropic_response(self, response: Any, context: PolicyContext) -> Any:
        return response

    async def on_anthropic_stream_event(
        self, event: AnthropicStreamEvent, context: PolicyContext
    ) -> list[AnthropicStreamEvent]:
        """First text delta -> [event], second -> [], third -> [event, extra]."""
        if isinstance(event, RawContentBlockDeltaEvent) and isinstance(event.delta, TextDelta):
            text = event.delta.text
            if text == "first":
                return [event]
            elif text == "second":
                return []
            elif text == "third":
                extra = make_text_delta_event("[bonus]", index=event.index)
                return [event, extra]
        return [event]


class TestAnthropicStreamExecutorMultiEvent:
    """Tests for multi-event returns from policies."""

    @pytest.mark.asyncio
    async def test_policy_returns_multiple_events_for_single_input(self, policy_ctx: PolicyContext):
        """Test that when a policy returns [event1, event2, event3], the executor yields all three in order."""
        executor = AnthropicStreamExecutor()
        policy = MultiEventPolicy()

        events: list[AnthropicStreamEvent] = [make_text_delta_event("Hello")]
        stream = async_iter_from_list(events)

        results = []
        async for result in executor.process(stream, policy, policy_ctx):
            results.append(result)

        assert len(results) == 3
        # First is the original
        assert isinstance(results[0], RawContentBlockDeltaEvent)
        assert isinstance(results[0].delta, TextDelta)
        assert results[0].delta.text == "Hello"
        # Second is extra1
        assert isinstance(results[1], RawContentBlockDeltaEvent)
        assert isinstance(results[1].delta, TextDelta)
        assert results[1].delta.text == "[extra1:Hello]"
        # Third is extra2
        assert isinstance(results[2], RawContentBlockDeltaEvent)
        assert isinstance(results[2].delta, TextDelta)
        assert results[2].delta.text == "[extra2:Hello]"

    @pytest.mark.asyncio
    async def test_mixed_single_empty_and_multi_event_returns(self, policy_ctx: PolicyContext):
        """Test [event1] for first, [] for second, [event2, event3] for third -> yields event1, event2, event3."""
        executor = AnthropicStreamExecutor()
        policy = SelectiveMultiEventPolicy()

        events: list[AnthropicStreamEvent] = [
            make_text_delta_event("first"),
            make_text_delta_event("second"),
            make_text_delta_event("third"),
        ]
        stream = async_iter_from_list(events)

        results = []
        async for result in executor.process(stream, policy, policy_ctx):
            results.append(result)

        assert len(results) == 3
        # "first" -> [event] -> 1 result
        assert isinstance(results[0], RawContentBlockDeltaEvent)
        assert isinstance(results[0].delta, TextDelta)
        assert results[0].delta.text == "first"
        # "second" -> [] -> 0 results (filtered)
        # "third" -> [event, extra] -> 2 results
        assert isinstance(results[1], RawContentBlockDeltaEvent)
        assert isinstance(results[1].delta, TextDelta)
        assert results[1].delta.text == "third"
        assert isinstance(results[2], RawContentBlockDeltaEvent)
        assert isinstance(results[2].delta, TextDelta)
        assert results[2].delta.text == "[bonus]"

    @pytest.mark.asyncio
    async def test_yielded_count_accounts_for_multi_event_returns(self, policy_ctx: PolicyContext):
        """Test that yielded_count span attribute counts all events from multi-event returns."""
        executor = AnthropicStreamExecutor()
        policy = MultiEventPolicy()

        # 2 text deltas, each expands to 3 events = 6 yielded
        # plus 1 message_start that passes through = 7 total
        events: list[AnthropicStreamEvent] = [
            make_message_start_event(),
            make_text_delta_event("A"),
            make_text_delta_event("B"),
        ]
        stream = async_iter_from_list(events)

        results = []
        async for result in executor.process(stream, policy, policy_ctx):
            results.append(result)

        # 1 (message_start) + 3 (A expanded) + 3 (B expanded) = 7
        assert len(results) == 7
