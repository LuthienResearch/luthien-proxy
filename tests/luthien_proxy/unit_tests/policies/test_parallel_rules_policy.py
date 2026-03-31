"""Unit tests for ParallelRulesPolicy."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from litellm.types.utils import Choices, Message, ModelResponse

from luthien_proxy.policies.parallel_rules_policy import (
    ParallelRulesConfig,
    ParallelRulesPolicy,
    Rule,
    _parse_rule_decision,
    _RuleResult,
)
from luthien_proxy.policies.simple_policy import SimplePolicy
from luthien_proxy.policy_core import AnthropicExecutionInterface
from luthien_proxy.policy_core.policy_context import PolicyContext


def _make_litellm_response(content: str) -> ModelResponse:
    """Create a mock LiteLLM ModelResponse."""
    return ModelResponse(
        id="test-id",
        choices=[Choices(finish_reason="stop", index=0, message=Message(content=content, role="assistant"))],
        created=1234567890,
        model="test-model",
        object="chat.completion",
    )


def _apply_response(rewritten: str) -> ModelResponse:
    return _make_litellm_response(json.dumps({"apply": True, "rewritten": rewritten}))


def _skip_response() -> ModelResponse:
    return _make_litellm_response(json.dumps({"apply": False}))


def _route_by_instruction(**kwargs) -> ModelResponse:
    """Route mock responses based on the rule instruction in the system prompt.

    Order-independent: uses prompt content, not call_count.
    Recognizes refinement calls by the "text editor merging" marker.
    """
    messages = kwargs.get("messages", [])
    system = messages[0]["content"] if messages else ""

    # Refinement call
    if "text editor merging" in system:
        return _make_litellm_response("MERGED RESULT")

    # Rule calls — keyed off the instruction text embedded in the prompt
    if "Uppercase" in system:
        return _apply_response("HELLO WORLD")
    if "Skip this" in system:
        return _skip_response()
    if "Apply A" in system:
        return _apply_response("VERSION A")
    if "Apply B" in system:
        return _apply_response("VERSION B")
    if "Transform" in system:
        return _apply_response("TRANSFORMED")

    # Default: skip
    return _skip_response()


# =============================================================================
# Parse decision tests
# =============================================================================


class TestParseRuleDecision:
    def test_apply_true_with_rewrite(self):
        assert _parse_rule_decision('{"apply": true, "rewritten": "NEW TEXT"}') == (True, "NEW TEXT")

    def test_apply_false(self):
        assert _parse_rule_decision('{"apply": false}') == (False, "")

    def test_with_markdown_fences(self):
        assert _parse_rule_decision('```json\n{"apply": true, "rewritten": "OK"}\n```') == (True, "OK")

    def test_with_extra_backtick_fences(self):
        assert _parse_rule_decision('````\n{"apply": true, "rewritten": "OK"}\n````') == (True, "OK")

    def test_preserves_backticks_in_content(self):
        """Backticks inside the rewritten text are not stripped."""
        result = _parse_rule_decision('{"apply": true, "rewritten": "use `foo` here"}')
        assert result == (True, "use `foo` here")

    def test_invalid_json_returns_none(self):
        assert _parse_rule_decision("not json") is None

    def test_missing_apply_key_returns_none(self):
        assert _parse_rule_decision('{"rewritten": "text"}') is None

    def test_non_dict_returns_none(self):
        assert _parse_rule_decision("[1, 2, 3]") is None


# =============================================================================
# Protocol compliance
# =============================================================================


class TestParallelRulesPolicyProtocol:
    def test_inherits_simple_policy(self):
        assert isinstance(ParallelRulesPolicy(), SimplePolicy)

    def test_implements_anthropic_interface(self):
        assert isinstance(ParallelRulesPolicy(), AnthropicExecutionInterface)

    def test_short_policy_name(self):
        assert ParallelRulesPolicy().short_policy_name == "ParallelRules"


# =============================================================================
# Config
# =============================================================================


class TestParallelRulesConfig:
    def test_default_config(self):
        config = ParallelRulesConfig()
        assert config.model == "claude-haiku-4-5"
        assert config.rules == []
        assert config.max_rules == 5

    def test_static_rules_parsed(self):
        policy = ParallelRulesPolicy(config={"rules": [{"name": "r1", "instruction": "Do thing"}]})
        assert len(policy._static_rules) == 1
        assert policy._static_rules[0].name == "r1"


# =============================================================================
# Rule application (all use instruction-based routing, not call_count)
# =============================================================================


class TestRuleApplication:
    @pytest.mark.asyncio
    async def test_no_rules_passthrough(self):
        policy = ParallelRulesPolicy()
        ctx = PolicyContext.for_testing()
        assert await policy.simple_on_response_content("Hello world", ctx) == "Hello world"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion", side_effect=_route_by_instruction)
    async def test_single_rule_applies(self, mock_acompletion):
        policy = ParallelRulesPolicy(config={"rules": [{"name": "up", "instruction": "Uppercase"}]})
        ctx = PolicyContext.for_testing()
        assert await policy.simple_on_response_content("Hello world", ctx) == "HELLO WORLD"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion", side_effect=_route_by_instruction)
    async def test_single_rule_skips(self, mock_acompletion):
        policy = ParallelRulesPolicy(config={"rules": [{"name": "noop", "instruction": "Skip this"}]})
        ctx = PolicyContext.for_testing()
        assert await policy.simple_on_response_content("Hello world", ctx) == "Hello world"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion", side_effect=_route_by_instruction)
    async def test_multiple_rules_all_skip(self, mock_acompletion):
        policy = ParallelRulesPolicy(
            config={"rules": [{"name": "s1", "instruction": "Skip this"}, {"name": "s2", "instruction": "Skip this"}]}
        )
        ctx = PolicyContext.for_testing()
        assert await policy.simple_on_response_content("Hello", ctx) == "Hello"
        assert mock_acompletion.call_count == 2

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion", side_effect=_route_by_instruction)
    async def test_one_applies_one_skips(self, mock_acompletion):
        """Order-independent: regardless of which coroutine finishes first."""
        policy = ParallelRulesPolicy(
            config={"rules": [{"name": "skip", "instruction": "Skip this"}, {"name": "up", "instruction": "Uppercase"}]}
        )
        ctx = PolicyContext.for_testing()
        assert await policy.simple_on_response_content("Hello", ctx) == "HELLO WORLD"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion", side_effect=_route_by_instruction)
    async def test_multiple_rules_trigger_refinement(self, mock_acompletion):
        policy = ParallelRulesPolicy(
            config={"rules": [{"name": "a", "instruction": "Apply A"}, {"name": "b", "instruction": "Apply B"}]}
        )
        ctx = PolicyContext.for_testing()
        result = await policy.simple_on_response_content("Hello", ctx)
        assert mock_acompletion.call_count == 3  # 2 rules + 1 refinement
        assert result == "MERGED RESULT"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion", side_effect=Exception("LLM error"))
    async def test_rule_failure_treated_as_skip(self, mock_acompletion):
        policy = ParallelRulesPolicy(config={"rules": [{"name": "fail", "instruction": "Fail"}]})
        ctx = PolicyContext.for_testing()
        assert await policy.simple_on_response_content("Hello", ctx) == "Hello"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_unparseable_response_treated_as_skip(self, mock_acompletion):
        mock_acompletion.return_value = _make_litellm_response("just some plain text")

        policy = ParallelRulesPolicy(config={"rules": [{"name": "bad", "instruction": "Bad"}]})
        ctx = PolicyContext.for_testing()
        assert await policy.simple_on_response_content("Hello", ctx) == "Hello"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_partial_failure_uses_successful_rule(self, mock_acompletion):
        """One rule throws, the other succeeds — order-independent."""

        def side_effect(**kwargs):
            system = kwargs["messages"][0]["content"]
            if "Fail" in system:
                raise Exception("Boom")
            return _apply_response("TRANSFORMED")

        mock_acompletion.side_effect = side_effect

        policy = ParallelRulesPolicy(
            config={"rules": [{"name": "fail", "instruction": "Fail"}, {"name": "ok", "instruction": "Transform"}]}
        )
        ctx = PolicyContext.for_testing()
        assert await policy.simple_on_response_content("Hello", ctx) == "TRANSFORMED"


# =============================================================================
# Dynamic rules
# =============================================================================


class TestDynamicRules:
    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion", side_effect=_route_by_instruction)
    async def test_dynamic_rules_override_static(self, mock_acompletion):
        policy = ParallelRulesPolicy(config={"rules": [{"name": "static", "instruction": "Skip this"}]})
        ctx = PolicyContext.for_testing()
        policy.set_rules_for_request(ctx, [Rule(name="dynamic", instruction="Uppercase")])
        result = await policy.simple_on_response_content("Hello", ctx)
        assert result == "HELLO WORLD"
        assert mock_acompletion.call_count == 1


# =============================================================================
# Config / credentials
# =============================================================================


class TestConfigWithApiCredentials:
    def test_config_stores_credentials(self):
        config = ParallelRulesConfig(model="custom", api_base="https://api.custom.com", api_key="sk-test")
        assert config.api_base == "https://api.custom.com"
        assert config.api_key == "sk-test"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_credentials_passed_to_llm(self, mock_acompletion):
        mock_acompletion.return_value = _skip_response()

        policy = ParallelRulesPolicy(
            config={
                "api_base": "https://api.custom.com",
                "api_key": "sk-test",
                "rules": [{"name": "t", "instruction": "T"}],
            }
        )
        ctx = PolicyContext.for_testing()
        await policy.simple_on_response_content("text", ctx)

        call_kwargs = mock_acompletion.call_args.kwargs
        assert call_kwargs["api_base"] == "https://api.custom.com"
        assert call_kwargs["api_key"] == "sk-test"


# =============================================================================
# Refinement failure
# =============================================================================


class TestRefinementFailureFallback:
    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_refine_failure_returns_original(self, mock_acompletion):
        """When refinement fails, return original text — not an arbitrary rule's output."""

        def side_effect(**kwargs):
            system = kwargs["messages"][0]["content"]
            if "text editor merging" in system:
                raise RuntimeError("Refinement failed")
            if "Apply A" in system:
                return _apply_response("VERSION A")
            return _apply_response("VERSION B")

        mock_acompletion.side_effect = side_effect

        policy = ParallelRulesPolicy(
            config={"rules": [{"name": "a", "instruction": "Apply A"}, {"name": "b", "instruction": "Apply B"}]}
        )
        ctx = PolicyContext.for_testing()
        result = await policy.simple_on_response_content("original text", ctx)
        assert result == "original text"


# =============================================================================
# Max rules
# =============================================================================


class TestMaxRules:
    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_max_rules_truncates(self, mock_acompletion):
        mock_acompletion.return_value = _skip_response()

        policy = ParallelRulesPolicy(
            config={
                "max_rules": 2,
                "rules": [
                    {"name": "r1", "instruction": "R1"},
                    {"name": "r2", "instruction": "R2"},
                    {"name": "r3", "instruction": "R3"},
                ],
            }
        )
        ctx = PolicyContext.for_testing()
        await policy.simple_on_response_content("text", ctx)
        assert mock_acompletion.call_count == 2


# =============================================================================
# Data structures
# =============================================================================


# =============================================================================
# Integration: run_anthropic end-to-end
# =============================================================================


class _StubIO:
    """Minimal IO for testing run_anthropic without MagicMock."""

    def __init__(self, response_text: str = "Original text"):
        self._request: dict = {
            "model": "claude-haiku-4-5-20251001",
            "messages": [{"role": "user", "content": "test"}],
            "max_tokens": 100,
            "stream": False,
        }
        self._response_text = response_text

    @property
    def request(self) -> dict:
        return self._request

    def set_request(self, request: dict) -> None:
        self._request = request

    @property
    def first_backend_response(self):
        return None

    async def complete(self, request=None):
        return {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": self._response_text}],
            "model": "claude-haiku-4-5-20251001",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

    def stream(self, request=None):
        async def _gen():
            return
            yield  # noqa: RET504 — makes this an async generator

        return _gen()


class TestRunAnthropicIntegration:
    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion", side_effect=_route_by_instruction)
    async def test_run_anthropic_applies_rules(self, mock_acompletion):
        policy = ParallelRulesPolicy(config={"rules": [{"name": "up", "instruction": "Uppercase"}]})
        io = _StubIO(response_text="Hello world")
        ctx = PolicyContext.for_testing()

        emissions = []
        async for emission in policy.run_anthropic(io, ctx):
            emissions.append(emission)

        assert len(emissions) == 1
        assert emissions[0]["content"][0]["text"] == "HELLO WORLD"

    @pytest.mark.asyncio
    async def test_run_anthropic_no_rules_passthrough(self):
        policy = ParallelRulesPolicy()
        io = _StubIO(response_text="Original text")
        ctx = PolicyContext.for_testing()

        emissions = []
        async for emission in policy.run_anthropic(io, ctx):
            emissions.append(emission)

        assert len(emissions) == 1
        assert emissions[0]["content"][0]["text"] == "Original text"


# =============================================================================
# Data structures
# =============================================================================


class TestRuleResultDataclass:
    def test_unchanged(self):
        result = _RuleResult(rule=Rule(name="t", instruction="i"), rewritten="hello", changed=False)
        assert result.changed is False

    def test_changed(self):
        result = _RuleResult(rule=Rule(name="t", instruction="i"), rewritten="HELLO", changed=True)
        assert result.changed is True
