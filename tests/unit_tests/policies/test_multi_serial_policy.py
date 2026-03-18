"""Unit tests for MultiSerialPolicy."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import pytest
from anthropic.types import (
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageStartEvent,
    TextBlock,
    TextDelta,
)
from multi_policy_helpers import (
    AnthropicOnlyPolicy,
    OpenAIOnlyPolicy,
    allcaps_config,
    make_anthropic_response,
    noop_config,
    replacement_config,
)

from conftest import DEFAULT_TEST_MODEL
from luthien_proxy.llm.types.anthropic import (
    AnthropicRequest,
    AnthropicResponse,
    AnthropicTextBlock,
)
from luthien_proxy.policies.multi_serial_policy import MultiSerialPolicy
from luthien_proxy.policy_core import (
    AnthropicExecutionInterface,
    BasePolicy,
)
from luthien_proxy.policy_core.policy_context import PolicyContext

# =============================================================================
# Protocol Compliance
# =============================================================================


class TestMultiSerialPolicyProtocol:
    def test_inherits_from_base_policy(self):
        policy = MultiSerialPolicy(policies=[noop_config()])
        assert isinstance(policy, BasePolicy)

    def test_implements_anthropic_execution_interface(self):
        policy = MultiSerialPolicy(policies=[noop_config()])
        assert isinstance(policy, AnthropicExecutionInterface)

    def test_policy_name_shows_sub_policies(self):
        policy = MultiSerialPolicy(policies=[noop_config(), allcaps_config()])
        assert "NoOp" in policy.short_policy_name
        assert "AllCapsPolicy" in policy.short_policy_name
        assert "MultiSerial" in policy.short_policy_name


# =============================================================================
# Initialization
# =============================================================================


class TestMultiSerialPolicyInit:
    def test_loads_single_policy(self):
        policy = MultiSerialPolicy(policies=[noop_config()])
        assert len(policy._sub_policies) == 1

    def test_loads_multiple_policies(self):
        policy = MultiSerialPolicy(policies=[noop_config(), allcaps_config()])
        assert len(policy._sub_policies) == 2

    def test_invalid_class_ref_raises(self):
        with pytest.raises((ImportError, ValueError)):
            MultiSerialPolicy(policies=[{"class": "nonexistent.module:Foo", "config": {}}])


# =============================================================================
# Anthropic Request Chaining
# =============================================================================


class TestMultiSerialAnthropicRequest:
    @pytest.mark.asyncio
    async def test_passes_through_with_noop(self):
        policy = MultiSerialPolicy(policies=[noop_config()])
        ctx = PolicyContext.for_testing()
        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
        }

        result = await policy.on_anthropic_request(request, ctx)

        assert result["messages"][0]["content"] == "Hello"


# =============================================================================
# Anthropic Response Chaining
# =============================================================================


class TestMultiSerialAnthropicResponse:
    @pytest.mark.asyncio
    async def test_allcaps_transforms_text(self):
        policy = MultiSerialPolicy(policies=[allcaps_config()])
        ctx = PolicyContext.for_testing()
        response = make_anthropic_response("hello world")

        result = await policy.on_anthropic_response(response, ctx)

        text_block = cast(AnthropicTextBlock, result["content"][0])
        assert text_block["text"] == "HELLO WORLD"

    @pytest.mark.asyncio
    async def test_chaining_two_transformations(self):
        """StringReplacement(hello->goodbye) then AllCaps -> 'GOODBYE WORLD'."""
        policy = MultiSerialPolicy(
            policies=[
                replacement_config([["hello", "goodbye"]]),
                allcaps_config(),
            ]
        )
        ctx = PolicyContext.for_testing()
        response = make_anthropic_response("hello world")

        result = await policy.on_anthropic_response(response, ctx)

        text_block = cast(AnthropicTextBlock, result["content"][0])
        assert text_block["text"] == "GOODBYE WORLD"


# =============================================================================
# Anthropic Stream Event Chaining
# =============================================================================


class TestMultiSerialAnthropicStreamEvent:
    @pytest.mark.asyncio
    async def test_text_delta_chained_through_allcaps(self):
        policy = MultiSerialPolicy(policies=[allcaps_config()])
        ctx = PolicyContext.for_testing()
        text_delta = TextDelta.model_construct(type="text_delta", text="hello")
        event = RawContentBlockDeltaEvent.model_construct(type="content_block_delta", index=0, delta=text_delta)

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert len(result) == 1
        result_event = cast(RawContentBlockDeltaEvent, result[0])
        assert isinstance(result_event.delta, TextDelta)
        assert result_event.delta.text == "HELLO"

    @pytest.mark.asyncio
    async def test_non_text_events_pass_through(self):
        policy = MultiSerialPolicy(policies=[allcaps_config()])
        ctx = PolicyContext.for_testing()
        event = RawMessageStartEvent.model_construct(
            type="message_start",
            message={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": "test",
                "stop_reason": None,
                "usage": {"input_tokens": 5, "output_tokens": 0},
            },
        )

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert len(result) == 1
        assert result[0] is event

    @pytest.mark.asyncio
    async def test_empty_policy_list_passes_events_through(self):
        policy = MultiSerialPolicy(policies=[])
        ctx = PolicyContext.for_testing()
        text_delta = TextDelta.model_construct(type="text_delta", text="hello")
        event = RawContentBlockDeltaEvent.model_construct(type="content_block_delta", index=0, delta=text_delta)

        result = await policy.on_anthropic_stream_event(event, ctx)

        assert len(result) == 1
        assert result[0] is event


# =============================================================================
# Anthropic run_anthropic — Response Ordering
# =============================================================================


class _StubAnthropicIO:
    """Minimal Anthropic I/O stub for run_anthropic ordering tests."""

    def __init__(self, text: str, stream: bool = False):
        self._text = text
        self._request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [],
            "max_tokens": 10,
            "stream": stream,
        }

    @property
    def request(self) -> AnthropicRequest:
        return self._request

    def set_request(self, request: AnthropicRequest) -> None:
        self._request = request

    @property
    def first_backend_response(self) -> AnthropicResponse | None:
        return None

    async def complete(self, request: AnthropicRequest | None = None) -> AnthropicResponse:
        return {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": self._text}],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

    def stream(self, request: AnthropicRequest | None = None) -> AsyncIterator:
        text = self._text
        events: list = [
            cast(
                RawContentBlockDeltaEvent,
                RawContentBlockStartEvent(
                    type="content_block_start",
                    index=0,
                    content_block=TextBlock(type="text", text=""),
                ),
            ),
            cast(
                RawContentBlockDeltaEvent,
                RawContentBlockDeltaEvent(
                    type="content_block_delta",
                    index=0,
                    delta=TextDelta(type="text_delta", text=text),
                ),
            ),
            cast(
                RawContentBlockDeltaEvent,
                RawContentBlockStopEvent(type="content_block_stop", index=0),
            ),
        ]

        async def _gen() -> AsyncIterator:
            for event in events:
                yield event

        return _gen()


class TestMultiSerialAnthropicRunOrdering:
    """Verify run_anthropic applies transforms in list order for both request and response."""

    @pytest.mark.asyncio
    async def test_non_streaming_response_order_matches_list_order(self):
        """[StringReplacement(hello->goodbye), AllCaps] must give GOODBYE WORLD.

        Regression: the old onion model ran AllCaps first on the response (because
        response hooks unwind in reverse), so StringReplacement's 'hello' pattern
        never matched the already-uppercased 'HELLO'. The two-phase model fixes this.
        """
        policy = MultiSerialPolicy(
            policies=[
                replacement_config([["hello", "goodbye"]]),
                allcaps_config(),
            ]
        )
        ctx = PolicyContext.for_testing()
        io = _StubAnthropicIO("hello world", stream=False)

        emissions = [e async for e in policy.run_anthropic(io, ctx)]

        text_block = cast(AnthropicTextBlock, emissions[0]["content"][0])
        assert text_block["text"] == "GOODBYE WORLD"

    @pytest.mark.asyncio
    async def test_streaming_response_order_matches_list_order(self):
        """Streaming: [StringReplacement(hello->goodbye), AllCaps] must give GOODBYE."""
        policy = MultiSerialPolicy(
            policies=[
                replacement_config([["hello", "goodbye"]]),
                allcaps_config(),
            ]
        )
        ctx = PolicyContext.for_testing()
        io = _StubAnthropicIO("hello", stream=True)

        emissions = [e async for e in policy.run_anthropic(io, ctx)]

        deltas = [e for e in emissions if isinstance(e, RawContentBlockDeltaEvent)]
        assert len(deltas) == 1
        assert deltas[0].delta.text == "GOODBYE"

    @pytest.mark.asyncio
    async def test_passthrough_with_empty_policy_list(self):
        """Empty policy list passes response through unchanged via run_anthropic."""
        policy = MultiSerialPolicy(policies=[])
        ctx = PolicyContext.for_testing()
        io = _StubAnthropicIO("hello world", stream=False)

        emissions = [e async for e in policy.run_anthropic(io, ctx)]

        text_block = cast(AnthropicTextBlock, emissions[0]["content"][0])
        assert text_block["text"] == "hello world"


# =============================================================================
# Anthropic Stream Lifecycle Hook Chaining
# =============================================================================


class TestMultiSerialAnthropicStreamLifecycle:
    @pytest.mark.asyncio
    async def test_stream_complete_delegates_to_subpolicies(self):
        class TrackingAnthropicPolicy(AnthropicOnlyPolicy):
            def __init__(self):
                super().__init__()
                self.complete_calls = 0

            async def on_anthropic_stream_complete(self, context: PolicyContext) -> None:
                self.complete_calls += 1

        tracking = TrackingAnthropicPolicy()
        policy = MultiSerialPolicy(policies=[])
        policy._sub_policies = [tracking]

        await policy.on_anthropic_stream_complete(PolicyContext.for_testing())

        assert tracking.complete_calls == 1

    @pytest.mark.asyncio
    async def test_streaming_policy_complete_delegates_to_subpolicies(self):
        class TrackingAnthropicPolicy(AnthropicOnlyPolicy):
            def __init__(self):
                super().__init__()
                self.cleanup_calls = 0

            async def on_anthropic_streaming_policy_complete(self, context: PolicyContext) -> None:
                self.cleanup_calls += 1

        tracking = TrackingAnthropicPolicy()
        policy = MultiSerialPolicy(policies=[])
        policy._sub_policies = [tracking]

        await policy.on_anthropic_streaming_policy_complete(PolicyContext.for_testing())

        assert tracking.cleanup_calls == 1


# =============================================================================
# Composability (Nested MultiSerialPolicy)
# =============================================================================


# =============================================================================
# Interface Validation
# =============================================================================


class TestMultiSerialInterfaceValidation:
    @pytest.mark.asyncio
    async def test_anthropic_request_raises_for_incompatible_policy(self):
        """Anthropic call raises TypeError when a sub-policy lacks AnthropicExecutionInterface."""
        policy = MultiSerialPolicy(policies=[noop_config()])
        policy._sub_policies = (*policy._sub_policies, OpenAIOnlyPolicy())
        ctx = PolicyContext.for_testing()
        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
        }

        with pytest.raises(TypeError, match="OpenAIOnly.*does not implement AnthropicExecutionInterface"):
            await policy.on_anthropic_request(request, ctx)

    @pytest.mark.asyncio
    async def test_anthropic_response_raises_for_incompatible_policy(self):
        """Anthropic response call raises TypeError for incompatible sub-policy."""
        policy = MultiSerialPolicy(policies=[noop_config()])
        policy._sub_policies = (*policy._sub_policies, OpenAIOnlyPolicy())
        ctx = PolicyContext.for_testing()
        response = make_anthropic_response("hello")

        with pytest.raises(TypeError, match="OpenAIOnly.*does not implement AnthropicExecutionInterface"):
            await policy.on_anthropic_response(response, ctx)

    @pytest.mark.asyncio
    async def test_anthropic_stream_event_raises_for_incompatible_policy(self):
        """Anthropic stream event raises TypeError for incompatible sub-policy."""
        policy = MultiSerialPolicy(policies=[noop_config()])
        policy._sub_policies = (*policy._sub_policies, OpenAIOnlyPolicy())
        ctx = PolicyContext.for_testing()
        text_delta = TextDelta.model_construct(type="text_delta", text="hello")
        event = RawContentBlockDeltaEvent.model_construct(type="content_block_delta", index=0, delta=text_delta)

        with pytest.raises(TypeError, match="OpenAIOnly.*does not implement AnthropicExecutionInterface"):
            await policy.on_anthropic_stream_event(event, ctx)

    @pytest.mark.asyncio
    async def test_all_compatible_policies_pass_validation(self):
        """No error when all sub-policies implement the required interface."""
        policy = MultiSerialPolicy(policies=[noop_config(), allcaps_config()])
        ctx = PolicyContext.for_testing()
        response = make_anthropic_response("hello")

        result = await policy.on_anthropic_response(response, ctx)

        text_block = cast(AnthropicTextBlock, result["content"][0])
        assert text_block["text"] == "HELLO"
