"""Unit tests for MultiSerialPolicy."""

from __future__ import annotations

from typing import cast

import pytest
from anthropic.types import RawContentBlockDeltaEvent, RawMessageStartEvent, TextDelta
from multi_policy_helpers import (
    AnthropicOnlyPolicy,
    OpenAIOnlyPolicy,
    allcaps_config,
    make_anthropic_response,
    make_response,
    noop_config,
    replacement_config,
)

from luthien_proxy.llm.types import Request
from luthien_proxy.llm.types.anthropic import (
    AnthropicRequest,
    AnthropicTextBlock,
)
from luthien_proxy.policies.multi_serial_policy import MultiSerialPolicy
from luthien_proxy.policy_core import (
    AnthropicPolicyInterface,
    BasePolicy,
    OpenAIPolicyInterface,
)
from luthien_proxy.policy_core.policy_context import PolicyContext

# =============================================================================
# Protocol Compliance
# =============================================================================


class TestMultiSerialPolicyProtocol:
    def test_inherits_from_base_policy(self):
        policy = MultiSerialPolicy(policies=[noop_config()])
        assert isinstance(policy, BasePolicy)

    def test_implements_openai_interface(self):
        policy = MultiSerialPolicy(policies=[noop_config()])
        assert isinstance(policy, OpenAIPolicyInterface)

    def test_implements_anthropic_interface(self):
        policy = MultiSerialPolicy(policies=[noop_config()])
        assert isinstance(policy, AnthropicPolicyInterface)

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
# OpenAI Request Chaining
# =============================================================================


class TestMultiSerialOpenAIRequest:
    @pytest.mark.asyncio
    async def test_single_noop_passes_through(self):
        policy = MultiSerialPolicy(policies=[noop_config()])
        ctx = PolicyContext.for_testing()
        request = Request(model="test", messages=[{"role": "user", "content": "hello"}])

        result = await policy.on_openai_request(request, ctx)

        assert result.messages[0]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_multiple_noops_pass_through(self):
        policy = MultiSerialPolicy(policies=[noop_config(), noop_config()])
        ctx = PolicyContext.for_testing()
        request = Request(model="test", messages=[{"role": "user", "content": "hello"}])

        result = await policy.on_openai_request(request, ctx)

        assert result.messages[0]["content"] == "hello"


# =============================================================================
# OpenAI Response Chaining
# =============================================================================


class TestMultiSerialOpenAIResponse:
    @pytest.mark.asyncio
    async def test_single_allcaps_transforms(self):
        policy = MultiSerialPolicy(policies=[allcaps_config()])
        ctx = PolicyContext.for_testing()
        response = make_response("hello world")

        result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "HELLO WORLD"

    @pytest.mark.asyncio
    async def test_chaining_order_matters(self):
        """StringReplacement first (hello->goodbye), then AllCaps -> 'GOODBYE WORLD'."""
        policy = MultiSerialPolicy(
            policies=[
                replacement_config([["hello", "goodbye"]]),
                allcaps_config(),
            ]
        )
        ctx = PolicyContext.for_testing()
        response = make_response("hello world")

        result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "GOODBYE WORLD"

    @pytest.mark.asyncio
    async def test_reverse_order_gives_different_result(self):
        """AllCaps first (hello->HELLO), then StringReplacement (hello->goodbye, case-sensitive)
        Since AllCaps already ran, 'hello' is now 'HELLO' and case-sensitive replacement
        for 'hello' won't match -> 'HELLO WORLD'."""
        policy = MultiSerialPolicy(
            policies=[
                allcaps_config(),
                replacement_config([["hello", "goodbye"]]),
            ]
        )
        ctx = PolicyContext.for_testing()
        response = make_response("hello world")

        result = await policy.on_openai_response(response, ctx)

        # AllCaps made it "HELLO WORLD", then case-sensitive replace of "hello" doesn't match
        assert result.choices[0].message.content == "HELLO WORLD"

    @pytest.mark.asyncio
    async def test_empty_policy_list_passes_through(self):
        policy = MultiSerialPolicy(policies=[])
        ctx = PolicyContext.for_testing()
        response = make_response("hello world")

        result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "hello world"


# =============================================================================
# Anthropic Request Chaining
# =============================================================================


class TestMultiSerialAnthropicRequest:
    @pytest.mark.asyncio
    async def test_passes_through_with_noop(self):
        policy = MultiSerialPolicy(policies=[noop_config()])
        ctx = PolicyContext.for_testing()
        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
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
# Composability (Nested MultiSerialPolicy)
# =============================================================================


class TestMultiSerialComposability:
    @pytest.mark.asyncio
    async def test_nested_serial_policies(self):
        """A MultiSerialPolicy containing another MultiSerialPolicy."""
        inner_config = {
            "class": "luthien_proxy.policies.multi_serial_policy:MultiSerialPolicy",
            "config": {"policies": [replacement_config([["hello", "goodbye"]])]},
        }
        policy = MultiSerialPolicy(policies=[inner_config, allcaps_config()])
        ctx = PolicyContext.for_testing()
        response = make_response("hello world")

        result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "GOODBYE WORLD"


# =============================================================================
# Interface Validation
# =============================================================================


class TestMultiSerialInterfaceValidation:
    @pytest.mark.asyncio
    async def test_openai_request_raises_for_incompatible_policy(self):
        """OpenAI call raises TypeError when a sub-policy lacks OpenAIPolicyInterface."""
        policy = MultiSerialPolicy(policies=[noop_config()])
        policy._sub_policies.append(AnthropicOnlyPolicy())
        ctx = PolicyContext.for_testing()
        request = Request(model="test", messages=[{"role": "user", "content": "hi"}])

        with pytest.raises(TypeError, match="AnthropicOnly.*does not implement OpenAIPolicyInterface"):
            await policy.on_openai_request(request, ctx)

    @pytest.mark.asyncio
    async def test_openai_response_raises_for_incompatible_policy(self):
        """OpenAI response call raises TypeError for incompatible sub-policy."""
        policy = MultiSerialPolicy(policies=[noop_config()])
        policy._sub_policies.append(AnthropicOnlyPolicy())
        ctx = PolicyContext.for_testing()
        response = make_response("hello")

        with pytest.raises(TypeError, match="AnthropicOnly.*does not implement OpenAIPolicyInterface"):
            await policy.on_openai_response(response, ctx)

    @pytest.mark.asyncio
    async def test_anthropic_request_raises_for_incompatible_policy(self):
        """Anthropic call raises TypeError when a sub-policy lacks AnthropicPolicyInterface."""
        policy = MultiSerialPolicy(policies=[noop_config()])
        policy._sub_policies.append(OpenAIOnlyPolicy())
        ctx = PolicyContext.for_testing()
        request: AnthropicRequest = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
        }

        with pytest.raises(TypeError, match="OpenAIOnly.*does not implement AnthropicPolicyInterface"):
            await policy.on_anthropic_request(request, ctx)

    @pytest.mark.asyncio
    async def test_anthropic_response_raises_for_incompatible_policy(self):
        """Anthropic response call raises TypeError for incompatible sub-policy."""
        policy = MultiSerialPolicy(policies=[noop_config()])
        policy._sub_policies.append(OpenAIOnlyPolicy())
        ctx = PolicyContext.for_testing()
        response = make_anthropic_response("hello")

        with pytest.raises(TypeError, match="OpenAIOnly.*does not implement AnthropicPolicyInterface"):
            await policy.on_anthropic_response(response, ctx)

    @pytest.mark.asyncio
    async def test_anthropic_stream_event_raises_for_incompatible_policy(self):
        """Anthropic stream event raises TypeError for incompatible sub-policy."""
        policy = MultiSerialPolicy(policies=[noop_config()])
        policy._sub_policies.append(OpenAIOnlyPolicy())
        ctx = PolicyContext.for_testing()
        text_delta = TextDelta.model_construct(type="text_delta", text="hello")
        event = RawContentBlockDeltaEvent.model_construct(type="content_block_delta", index=0, delta=text_delta)

        with pytest.raises(TypeError, match="OpenAIOnly.*does not implement AnthropicPolicyInterface"):
            await policy.on_anthropic_stream_event(event, ctx)

    @pytest.mark.asyncio
    async def test_all_compatible_policies_pass_validation(self):
        """No error when all sub-policies implement the required interface."""
        policy = MultiSerialPolicy(policies=[noop_config(), allcaps_config()])
        ctx = PolicyContext.for_testing()
        response = make_response("hello")

        result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "HELLO"

    @pytest.mark.asyncio
    async def test_empty_policy_list_passes_validation(self):
        """Empty policy list doesn't raise -- nothing to validate."""
        policy = MultiSerialPolicy(policies=[])
        ctx = PolicyContext.for_testing()
        request = Request(model="test", messages=[{"role": "user", "content": "hi"}])

        result = await policy.on_openai_request(request, ctx)

        assert result.messages[0]["content"] == "hi"
