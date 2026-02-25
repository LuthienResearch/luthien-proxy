"""Unit tests for MultiParallelPolicy."""

from __future__ import annotations

import asyncio
from typing import cast
from unittest.mock import AsyncMock

import pytest
from anthropic.types import RawContentBlockDeltaEvent, TextDelta
from multi_policy_helpers import (
    AnthropicOnlyPolicy,
    OpenAIOnlyPolicy,
    allcaps_config,
    make_anthropic_response,
    make_response,
    noop_config,
    replacement_config,
)

from conftest import DEFAULT_TEST_MODEL
from luthien_proxy.llm.types import Request
from luthien_proxy.llm.types.anthropic import (
    AnthropicRequest,
    AnthropicTextBlock,
)
from luthien_proxy.policies.multi_parallel_policy import MultiParallelPolicy
from luthien_proxy.policy_core import (
    AnthropicPolicyInterface,
    BasePolicy,
    OpenAIPolicyInterface,
)
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext
from luthien_proxy.streaming.stream_state import StreamState

# =============================================================================
# Protocol Compliance
# =============================================================================


class TestMultiParallelPolicyProtocol:
    def test_inherits_from_base_policy(self):
        policy = MultiParallelPolicy(policies=[noop_config()])
        assert isinstance(policy, BasePolicy)

    def test_implements_openai_interface(self):
        policy = MultiParallelPolicy(policies=[noop_config()])
        assert isinstance(policy, OpenAIPolicyInterface)

    def test_implements_anthropic_interface(self):
        policy = MultiParallelPolicy(policies=[noop_config()])
        assert isinstance(policy, AnthropicPolicyInterface)

    def test_policy_name_shows_strategy_and_sub_policies(self):
        policy = MultiParallelPolicy(
            policies=[noop_config(), allcaps_config()],
            consolidation_strategy="first_block",
        )
        name = policy.short_policy_name
        assert "MultiParallel" in name
        assert "first_block" in name
        assert "NoOp" in name
        assert "AllCapsPolicy" in name


# =============================================================================
# Initialization
# =============================================================================


class TestMultiParallelPolicyInit:
    def test_valid_strategies_accepted(self):
        for strategy in ("first_block", "most_restrictive", "unanimous_pass", "majority_pass"):
            policy = MultiParallelPolicy(policies=[noop_config()], consolidation_strategy=strategy)
            assert policy._strategy == strategy

    def test_designated_strategy_accepted(self):
        policy = MultiParallelPolicy(
            policies=[noop_config()],
            consolidation_strategy="designated",
            designated_policy_index=0,
        )
        assert policy._strategy == "designated"
        assert policy._designated_policy_index == 0

    def test_designated_requires_index(self):
        with pytest.raises(ValueError, match="designated_policy_index is required"):
            MultiParallelPolicy(policies=[noop_config()], consolidation_strategy="designated")

    def test_designated_index_out_of_range(self):
        with pytest.raises(ValueError, match="out of range"):
            MultiParallelPolicy(
                policies=[noop_config()],
                consolidation_strategy="designated",
                designated_policy_index=5,
            )

    def test_invalid_strategy_raises(self):
        with pytest.raises(ValueError, match="Unknown consolidation_strategy"):
            MultiParallelPolicy(policies=[noop_config()], consolidation_strategy="invalid")

    def test_default_strategy_is_first_block(self):
        policy = MultiParallelPolicy(policies=[noop_config()])
        assert policy._strategy == "first_block"


# =============================================================================
# Streaming Not Supported
# =============================================================================


class TestMultiParallelStreamingNotSupported:
    @pytest.fixture
    def policy(self):
        return MultiParallelPolicy(policies=[noop_config()])

    @pytest.fixture
    def streaming_ctx(self):
        stream_state = StreamState()
        policy_ctx = PolicyContext.for_testing()
        return StreamingPolicyContext(
            policy_ctx=policy_ctx,
            egress_queue=asyncio.Queue(),
            original_streaming_response_state=stream_state,
            keepalive=lambda: None,
        )

    @pytest.mark.asyncio
    async def test_on_chunk_received_raises(self, policy, streaming_ctx):
        with pytest.raises(NotImplementedError, match="does not support streaming"):
            await policy.on_chunk_received(streaming_ctx)

    @pytest.mark.asyncio
    async def test_on_content_delta_raises(self, policy, streaming_ctx):
        with pytest.raises(NotImplementedError, match="does not support streaming"):
            await policy.on_content_delta(streaming_ctx)

    @pytest.mark.asyncio
    async def test_on_stream_complete_raises(self, policy, streaming_ctx):
        with pytest.raises(NotImplementedError, match="does not support streaming"):
            await policy.on_stream_complete(streaming_ctx)

    @pytest.mark.asyncio
    async def test_anthropic_stream_event_raises(self, policy):
        ctx = PolicyContext.for_testing()
        text_delta = TextDelta.model_construct(type="text_delta", text="hello")
        event = RawContentBlockDeltaEvent.model_construct(type="content_block_delta", index=0, delta=text_delta)
        with pytest.raises(NotImplementedError, match="does not support Anthropic streaming"):
            await policy.on_anthropic_stream_event(event, ctx)


# =============================================================================
# OpenAI Response - first_block Strategy
# =============================================================================


class TestMultiParallelFirstBlock:
    @pytest.mark.asyncio
    async def test_all_noop_passes_through(self):
        """When no policy modifies the response, original passes through."""
        policy = MultiParallelPolicy(
            policies=[noop_config(), noop_config()],
            consolidation_strategy="first_block",
        )
        ctx = PolicyContext.for_testing()
        response = make_response("hello world")

        result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "hello world"

    @pytest.mark.asyncio
    async def test_one_modifier_wins(self):
        """When one policy modifies, that result is used."""
        policy = MultiParallelPolicy(
            policies=[noop_config(), allcaps_config()],
            consolidation_strategy="first_block",
        )
        ctx = PolicyContext.for_testing()
        response = make_response("hello world")

        result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "HELLO WORLD"

    @pytest.mark.asyncio
    async def test_first_modifier_wins_when_multiple_modify(self):
        """When multiple policies modify, the first one's result is used."""
        policy = MultiParallelPolicy(
            policies=[
                allcaps_config(),
                replacement_config([["hello", "goodbye"]]),
            ],
            consolidation_strategy="first_block",
        )
        ctx = PolicyContext.for_testing()
        response = make_response("hello world")

        result = await policy.on_openai_response(response, ctx)

        # AllCaps is first policy that modifies -> "HELLO WORLD"
        assert result.choices[0].message.content == "HELLO WORLD"


# =============================================================================
# OpenAI Response - most_restrictive Strategy
# =============================================================================


class TestMultiParallelMostRestrictive:
    @pytest.mark.asyncio
    async def test_shorter_response_wins(self):
        """The policy producing the shortest content wins."""
        policy = MultiParallelPolicy(
            policies=[
                allcaps_config(),  # "HELLO WORLD" (11 chars)
                replacement_config([["hello world", "blocked"]]),  # "blocked" (7 chars)
            ],
            consolidation_strategy="most_restrictive",
        )
        ctx = PolicyContext.for_testing()
        response = make_response("hello world")

        result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "blocked"

    @pytest.mark.asyncio
    async def test_all_noop_passes_through(self):
        policy = MultiParallelPolicy(
            policies=[noop_config(), noop_config()],
            consolidation_strategy="most_restrictive",
        )
        ctx = PolicyContext.for_testing()
        response = make_response("hello world")

        result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "hello world"


# =============================================================================
# OpenAI Response - unanimous_pass Strategy
# =============================================================================


class TestMultiParallelUnanimousPass:
    @pytest.mark.asyncio
    async def test_all_noop_passes_through(self):
        policy = MultiParallelPolicy(
            policies=[noop_config(), noop_config()],
            consolidation_strategy="unanimous_pass",
        )
        ctx = PolicyContext.for_testing()
        response = make_response("hello world")

        result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "hello world"

    @pytest.mark.asyncio
    async def test_any_modifier_blocks(self):
        """If any policy modifies, the first modified result is used."""
        policy = MultiParallelPolicy(
            policies=[noop_config(), allcaps_config()],
            consolidation_strategy="unanimous_pass",
        )
        ctx = PolicyContext.for_testing()
        response = make_response("hello world")

        result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "HELLO WORLD"


# =============================================================================
# OpenAI Response - majority_pass Strategy
# =============================================================================


class TestMultiParallelMajorityPass:
    @pytest.mark.asyncio
    async def test_majority_noop_passes_through(self):
        """2 out of 3 policies pass -> original passes through."""
        policy = MultiParallelPolicy(
            policies=[noop_config(), noop_config(), allcaps_config()],
            consolidation_strategy="majority_pass",
        )
        ctx = PolicyContext.for_testing()
        response = make_response("hello world")

        result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "hello world"

    @pytest.mark.asyncio
    async def test_majority_modifiers_blocks(self):
        """2 out of 3 policies modify -> first modified result is used."""
        policy = MultiParallelPolicy(
            policies=[
                allcaps_config(),
                replacement_config([["hello", "goodbye"]]),
                noop_config(),
            ],
            consolidation_strategy="majority_pass",
        )
        ctx = PolicyContext.for_testing()
        response = make_response("hello world")

        result = await policy.on_openai_response(response, ctx)

        # Majority (2/3) modified, first modifier (AllCaps) wins
        assert result.choices[0].message.content == "HELLO WORLD"

    @pytest.mark.asyncio
    async def test_tie_uses_modified(self):
        """When exactly half modify (not a strict majority of passes), modified wins."""
        policy = MultiParallelPolicy(
            policies=[noop_config(), allcaps_config()],
            consolidation_strategy="majority_pass",
        )
        ctx = PolicyContext.for_testing()
        response = make_response("hello world")

        result = await policy.on_openai_response(response, ctx)

        # 1 pass, 1 modify -> not a strict majority of passes -> modified wins
        assert result.choices[0].message.content == "HELLO WORLD"


# =============================================================================
# OpenAI Response - designated Strategy
# =============================================================================


class TestMultiParallelDesignated:
    @pytest.mark.asyncio
    async def test_designated_policy_modifies_response(self):
        """When the designated policy modifies the response, use its result."""
        policy = MultiParallelPolicy(
            policies=[noop_config(), allcaps_config()],
            consolidation_strategy="designated",
            designated_policy_index=1,
        )
        ctx = PolicyContext.for_testing()
        response = make_response("hello world")

        result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "HELLO WORLD"

    @pytest.mark.asyncio
    async def test_designated_noop_returns_original(self):
        """When the designated policy doesn't modify, return the original."""
        policy = MultiParallelPolicy(
            policies=[allcaps_config(), noop_config()],
            consolidation_strategy="designated",
            designated_policy_index=1,
        )
        ctx = PolicyContext.for_testing()
        response = make_response("hello world")

        result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "hello world"

    @pytest.mark.asyncio
    async def test_designated_ignores_other_modifiers(self):
        """Only the designated policy's result matters, others are ignored."""
        policy = MultiParallelPolicy(
            policies=[
                allcaps_config(),
                replacement_config([["hello", "goodbye"]]),
                noop_config(),
            ],
            consolidation_strategy="designated",
            designated_policy_index=1,
        )
        ctx = PolicyContext.for_testing()
        response = make_response("hello world")

        result = await policy.on_openai_response(response, ctx)

        # Designated is index 1 (replacement policy), AllCaps at index 0 is ignored
        assert result.choices[0].message.content == "goodbye world"

    @pytest.mark.asyncio
    async def test_designated_request_noop_returns_original(self):
        """When designated policy doesn't modify request, return original."""
        policy = MultiParallelPolicy(
            policies=[noop_config(), noop_config()],
            consolidation_strategy="designated",
            designated_policy_index=1,
        )
        ctx = PolicyContext.for_testing()
        request = Request(model="test", messages=[{"role": "user", "content": "hello"}])

        result = await policy.on_openai_request(request, ctx)

        assert result.messages[0]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_designated_anthropic_response(self):
        """Designated strategy works for Anthropic responses."""
        policy = MultiParallelPolicy(
            policies=[noop_config(), allcaps_config()],
            consolidation_strategy="designated",
            designated_policy_index=1,
        )
        ctx = PolicyContext.for_testing()
        response = make_anthropic_response("hello world")

        result = await policy.on_anthropic_response(response, ctx)

        text_block = cast(AnthropicTextBlock, result["content"][0])
        assert text_block["text"] == "HELLO WORLD"

    @pytest.mark.asyncio
    async def test_designated_anthropic_response_noop_returns_original(self):
        """When designated Anthropic policy doesn't modify, return original."""
        policy = MultiParallelPolicy(
            policies=[allcaps_config(), noop_config()],
            consolidation_strategy="designated",
            designated_policy_index=1,
        )
        ctx = PolicyContext.for_testing()
        response = make_anthropic_response("hello world")

        result = await policy.on_anthropic_response(response, ctx)

        text_block = cast(AnthropicTextBlock, result["content"][0])
        assert text_block["text"] == "hello world"

    @pytest.mark.asyncio
    async def test_incompatible_sub_policy_raises_on_anthropic_call(self):
        """When a sub-policy doesn't implement AnthropicPolicyInterface, raise TypeError."""
        policy = MultiParallelPolicy(
            policies=[noop_config(), allcaps_config()],
            consolidation_strategy="designated",
            designated_policy_index=1,
        )

        # Replace the first sub-policy with an OpenAI-only stub
        policy._sub_policies[0] = OpenAIOnlyPolicy()

        ctx = PolicyContext.for_testing()
        response = make_anthropic_response("hello world")

        with pytest.raises(TypeError, match="OpenAIOnly.*does not implement AnthropicPolicyInterface"):
            await policy.on_anthropic_response(response, ctx)


# =============================================================================
# OpenAI Request
# =============================================================================


class TestMultiParallelOpenAIRequest:
    @pytest.mark.asyncio
    async def test_noop_passes_request_through(self):
        policy = MultiParallelPolicy(policies=[noop_config()])
        ctx = PolicyContext.for_testing()
        request = Request(model="test", messages=[{"role": "user", "content": "hello"}])

        result = await policy.on_openai_request(request, ctx)

        assert result.messages[0]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_empty_policy_list_passes_through(self):
        policy = MultiParallelPolicy(policies=[])
        ctx = PolicyContext.for_testing()
        response = make_response("hello world")

        result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "hello world"


# =============================================================================
# Anthropic Response
# =============================================================================


class TestMultiParallelAnthropicResponse:
    @pytest.mark.asyncio
    async def test_first_block_with_modifier(self):
        policy = MultiParallelPolicy(
            policies=[noop_config(), allcaps_config()],
            consolidation_strategy="first_block",
        )
        ctx = PolicyContext.for_testing()
        response = make_anthropic_response("hello world")

        result = await policy.on_anthropic_response(response, ctx)

        text_block = cast(AnthropicTextBlock, result["content"][0])
        assert text_block["text"] == "HELLO WORLD"

    @pytest.mark.asyncio
    async def test_all_noop_passes_through(self):
        policy = MultiParallelPolicy(
            policies=[noop_config(), noop_config()],
            consolidation_strategy="first_block",
        )
        ctx = PolicyContext.for_testing()
        response = make_anthropic_response("hello world")

        result = await policy.on_anthropic_response(response, ctx)

        text_block = cast(AnthropicTextBlock, result["content"][0])
        assert text_block["text"] == "hello world"

    @pytest.mark.asyncio
    async def test_most_restrictive_picks_shorter(self):
        policy = MultiParallelPolicy(
            policies=[
                allcaps_config(),  # "HELLO WORLD" (11 chars)
                replacement_config([["hello world", "no"]]),  # "no" (2 chars)
            ],
            consolidation_strategy="most_restrictive",
        )
        ctx = PolicyContext.for_testing()
        response = make_anthropic_response("hello world")

        result = await policy.on_anthropic_response(response, ctx)

        text_block = cast(AnthropicTextBlock, result["content"][0])
        assert text_block["text"] == "no"


# =============================================================================
# Anthropic Request
# =============================================================================


class TestMultiParallelAnthropicRequest:
    @pytest.mark.asyncio
    async def test_noop_passes_through(self):
        policy = MultiParallelPolicy(policies=[noop_config()])
        ctx = PolicyContext.for_testing()
        request: AnthropicRequest = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 100,
        }

        result = await policy.on_anthropic_request(request, ctx)

        assert result["messages"][0]["content"] == "Hello"


# =============================================================================
# Composability
# =============================================================================


class TestMultiParallelComposability:
    @pytest.mark.asyncio
    async def test_nested_in_serial(self):
        """MultiParallelPolicy nested inside MultiSerialPolicy."""
        from luthien_proxy.policies.multi_serial_policy import MultiSerialPolicy

        parallel_config = {
            "class": "luthien_proxy.policies.multi_parallel_policy:MultiParallelPolicy",
            "config": {
                "consolidation_strategy": "first_block",
                "policies": [noop_config(), allcaps_config()],
            },
        }
        policy = MultiSerialPolicy(
            policies=[
                parallel_config,
                replacement_config([["WORLD", "UNIVERSE"]]),
            ]
        )
        ctx = PolicyContext.for_testing()
        response = make_response("hello world")

        result = await policy.on_openai_response(response, ctx)

        # Parallel: AllCaps wins -> "HELLO WORLD"
        # Then serial: replace WORLD -> UNIVERSE -> "HELLO UNIVERSE"
        assert result.choices[0].message.content == "HELLO UNIVERSE"


# =============================================================================
# Interface Validation
# =============================================================================


class TestMultiParallelInterfaceValidation:
    @pytest.mark.asyncio
    async def test_openai_request_raises_for_incompatible_policy(self):
        """OpenAI call raises TypeError when a sub-policy lacks OpenAIPolicyInterface."""
        policy = MultiParallelPolicy(policies=[noop_config()])
        policy._sub_policies.append(AnthropicOnlyPolicy())
        ctx = PolicyContext.for_testing()
        request = Request(model="test", messages=[{"role": "user", "content": "hi"}])

        with pytest.raises(TypeError, match="AnthropicOnly.*does not implement OpenAIPolicyInterface"):
            await policy.on_openai_request(request, ctx)

    @pytest.mark.asyncio
    async def test_anthropic_response_raises_for_incompatible_policy(self):
        """Anthropic call raises TypeError when a sub-policy lacks AnthropicPolicyInterface."""
        policy = MultiParallelPolicy(policies=[noop_config()])
        policy._sub_policies.append(OpenAIOnlyPolicy())
        ctx = PolicyContext.for_testing()
        response = make_anthropic_response("hello")

        with pytest.raises(TypeError, match="OpenAIOnly.*does not implement AnthropicPolicyInterface"):
            await policy.on_anthropic_response(response, ctx)

    @pytest.mark.asyncio
    async def test_all_compatible_policies_pass_validation(self):
        """No error when all sub-policies implement the required interface."""
        policy = MultiParallelPolicy(policies=[noop_config(), allcaps_config()])
        ctx = PolicyContext.for_testing()
        response = make_response("hello")

        result = await policy.on_openai_response(response, ctx)

        assert result.choices[0].message.content == "HELLO"


# =============================================================================
# Parallel Execution Verification
# =============================================================================


class TestMultiParallelExecution:
    @pytest.mark.asyncio
    async def test_policies_receive_independent_copies(self):
        """Each sub-policy should receive its own copy, not share the same object."""
        policy = MultiParallelPolicy(
            policies=[allcaps_config(), replacement_config([["hello", "goodbye"]])],
            consolidation_strategy="first_block",
        )
        ctx = PolicyContext.for_testing()
        response = make_response("hello world")

        result = await policy.on_openai_response(response, ctx)

        # AllCaps is the first modifier -> "HELLO WORLD"
        # The replacement policy got its own copy and made "goodbye world"
        # but AllCaps result is picked because it's first
        assert result.choices[0].message.content == "HELLO WORLD"

    @pytest.mark.asyncio
    async def test_sub_policy_exception_fails_entire_batch(self):
        """If any sub-policy raises, the entire parallel execution fails."""
        policy = MultiParallelPolicy(
            policies=[noop_config(), allcaps_config()],
            consolidation_strategy="first_block",
        )
        ctx = PolicyContext.for_testing()
        response = make_response("hello world")

        # Patch one sub-policy to raise
        policy._sub_policies[1].on_openai_response = AsyncMock(side_effect=RuntimeError("policy exploded"))

        with pytest.raises(RuntimeError, match="policy exploded"):
            await policy.on_openai_response(response, ctx)

    @pytest.mark.asyncio
    async def test_sub_policy_exception_fails_entire_batch_anthropic(self):
        """If any sub-policy raises on Anthropic path, the entire parallel execution fails."""
        policy = MultiParallelPolicy(
            policies=[noop_config(), allcaps_config()],
            consolidation_strategy="first_block",
        )
        ctx = PolicyContext.for_testing()
        response = make_anthropic_response("hello world")

        policy._sub_policies[1].on_anthropic_response = AsyncMock(side_effect=RuntimeError("anthropic exploded"))

        with pytest.raises(RuntimeError, match="anthropic exploded"):
            await policy.on_anthropic_response(response, ctx)
