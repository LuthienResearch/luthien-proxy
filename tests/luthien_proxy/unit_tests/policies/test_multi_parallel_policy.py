"""Unit tests for MultiParallelPolicy."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock

import pytest
from anthropic.types import RawContentBlockDeltaEvent, TextDelta
from multi_policy_helpers import (
    OpenAIOnlyPolicy,
    allcaps_config,
    make_anthropic_response,
    noop_config,
    replacement_config,
)

from tests.constants import DEFAULT_TEST_MODEL
from luthien_proxy.llm.types.anthropic import (
    AnthropicRequest,
    AnthropicTextBlock,
)
from luthien_proxy.policies.multi_parallel_policy import MultiParallelPolicy
from luthien_proxy.policy_core import (
    AnthropicExecutionInterface,
    BasePolicy,
)
from luthien_proxy.policy_core.policy_context import PolicyContext

# =============================================================================
# Protocol Compliance
# =============================================================================


class TestMultiParallelPolicyProtocol:
    def test_inherits_from_base_policy(self):
        policy = MultiParallelPolicy(policies=[noop_config()])
        assert isinstance(policy, BasePolicy)

    def test_implements_anthropic_execution_interface(self):
        policy = MultiParallelPolicy(policies=[noop_config()])
        assert isinstance(policy, AnthropicExecutionInterface)

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
# Anthropic Stream Event Tests
# =============================================================================


class TestMultiParallelAnthropicStreamEventNotSupported:
    @pytest.mark.asyncio
    async def test_anthropic_stream_event_raises(self):
        policy = MultiParallelPolicy(policies=[noop_config()])
        ctx = PolicyContext.for_testing()
        text_delta = TextDelta.model_construct(type="text_delta", text="hello")
        event = RawContentBlockDeltaEvent.model_construct(type="content_block_delta", index=0, delta=text_delta)
        with pytest.raises(NotImplementedError, match="does not support Anthropic streaming"):
            await policy.on_anthropic_stream_event(event, ctx)


# =============================================================================
# Anthropic Response - designated Strategy
# =============================================================================


class TestMultiParallelDesignated:
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
        """When a sub-policy doesn't implement AnthropicExecutionInterface, raise TypeError."""
        policy = MultiParallelPolicy(
            policies=[noop_config(), allcaps_config()],
            consolidation_strategy="designated",
            designated_policy_index=1,
        )

        # Replace sub-policies tuple to inject an incompatible policy
        policy._sub_policies = (OpenAIOnlyPolicy(), policy._sub_policies[1])

        ctx = PolicyContext.for_testing()
        response = make_anthropic_response("hello world")

        with pytest.raises(TypeError, match="OpenAIOnly.*does not implement AnthropicExecutionInterface"):
            await policy.on_anthropic_response(response, ctx)


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


# =============================================================================
# Interface Validation
# =============================================================================


class TestMultiParallelInterfaceValidation:
    @pytest.mark.asyncio
    async def test_anthropic_response_raises_for_incompatible_policy(self):
        """Anthropic call raises TypeError when a sub-policy lacks AnthropicExecutionInterface."""
        policy = MultiParallelPolicy(policies=[noop_config()])
        policy._sub_policies = (*policy._sub_policies, OpenAIOnlyPolicy())
        ctx = PolicyContext.for_testing()
        response = make_anthropic_response("hello")

        with pytest.raises(TypeError, match="OpenAIOnly.*does not implement AnthropicExecutionInterface"):
            await policy.on_anthropic_response(response, ctx)

    @pytest.mark.asyncio
    async def test_all_compatible_policies_pass_validation(self):
        """No error when all sub-policies implement the required interface."""
        policy = MultiParallelPolicy(policies=[noop_config(), allcaps_config()])
        ctx = PolicyContext.for_testing()
        response = make_anthropic_response("hello")

        result = await policy.on_anthropic_response(response, ctx)

        text_block = cast(AnthropicTextBlock, result["content"][0])
        assert text_block["text"] == "HELLO"


# =============================================================================
# Parallel Execution Verification
# =============================================================================


class TestMultiParallelExecution:
    @pytest.mark.asyncio
    async def test_sub_policy_exception_fails_entire_batch_anthropic(self):
        """If any sub-policy raises on Anthropic path, the entire parallel execution fails."""
        policy = MultiParallelPolicy(
            policies=[noop_config(), allcaps_config()],
            consolidation_strategy="first_block",
        )
        ctx = PolicyContext.for_testing()
        response = make_anthropic_response("hello world")

        object.__setattr__(
            policy._sub_policies[1],
            "on_anthropic_response",
            AsyncMock(side_effect=RuntimeError("anthropic exploded")),
        )

        with pytest.raises(RuntimeError, match="anthropic exploded"):
            await policy.on_anthropic_response(response, ctx)


class TestPolicyContextDeepCopy:
    def test_deepcopy_succeeds_with_emitter(self):
        """PolicyContext with a real EventEmitter (holding db/redis pools) must be deep-copyable.

        Regression test for: MultiParallelPolicy crashed with 500 because
        copy.deepcopy(context) failed on asyncpg objects inside the emitter.
        """
        import copy

        from luthien_proxy.observability.emitter import EventEmitter

        emitter = EventEmitter(db_pool=None, event_publisher=None)
        ctx = PolicyContext(transaction_id="test-txn", emitter=emitter)
        ctx._scratchpad["key"] = "value"

        ctx_copy = copy.deepcopy(ctx)

        assert ctx_copy.transaction_id == ctx.transaction_id
        assert ctx_copy._emitter is ctx._emitter  # shared, not copied
        assert ctx_copy._scratchpad == ctx._scratchpad
        assert ctx_copy._scratchpad is not ctx._scratchpad  # independent copy

    def test_deepcopy_mutable_state_is_independent(self):
        """Mutating a deepcopied context must not affect the original."""
        import copy

        ctx = PolicyContext.for_testing(transaction_id="original")
        ctx._scratchpad["x"] = 1

        ctx_copy = copy.deepcopy(ctx)
        ctx_copy._scratchpad["x"] = 99
        ctx_copy.request_summary = "modified"

        assert ctx._scratchpad["x"] == 1
        assert ctx.request_summary is None
