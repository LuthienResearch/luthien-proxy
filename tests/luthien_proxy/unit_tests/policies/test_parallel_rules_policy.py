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
    _RuleResult,
    _parse_rule_decision,
)
from luthien_proxy.policies.simple_policy import SimplePolicy
from luthien_proxy.policy_core import AnthropicExecutionInterface
from luthien_proxy.policy_core.policy_context import PolicyContext


def _make_litellm_response(content: str) -> ModelResponse:
    """Create a mock LiteLLM ModelResponse."""
    return ModelResponse(
        id="test-id",
        choices=[
            Choices(
                finish_reason="stop",
                index=0,
                message=Message(content=content, role="assistant"),
            )
        ],
        created=1234567890,
        model="test-model",
        object="chat.completion",
    )


def _apply_response(rewritten: str) -> ModelResponse:
    """Create a structured rule response that applies a rewrite."""
    return _make_litellm_response(json.dumps({"apply": True, "rewritten": rewritten}))


def _skip_response() -> ModelResponse:
    """Create a structured rule response that skips (rule doesn't apply)."""
    return _make_litellm_response(json.dumps({"apply": False}))


# =============================================================================
# Parse decision tests
# =============================================================================


class TestParseRuleDecision:
    """Tests for the structured response parser."""

    def test_apply_true_with_rewrite(self):
        result = _parse_rule_decision('{"apply": true, "rewritten": "NEW TEXT"}')
        assert result == (True, "NEW TEXT")

    def test_apply_false(self):
        result = _parse_rule_decision('{"apply": false}')
        assert result == (False, "")

    def test_with_markdown_fences(self):
        result = _parse_rule_decision('```json\n{"apply": true, "rewritten": "OK"}\n```')
        assert result == (True, "OK")

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
        assert config.temperature == 0.0

    def test_static_rules_parsed(self):
        policy = ParallelRulesPolicy(config={"rules": [{"name": "r1", "instruction": "Do thing"}]})
        assert len(policy._static_rules) == 1
        assert policy._static_rules[0].name == "r1"


# =============================================================================
# Rule application
# =============================================================================


class TestRuleApplication:
    @pytest.mark.asyncio
    async def test_no_rules_passthrough(self):
        policy = ParallelRulesPolicy()
        ctx = PolicyContext.for_testing()
        result = await policy.simple_on_response_content("Hello world", ctx)
        assert result == "Hello world"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_single_rule_applies(self, mock_acompletion):
        mock_acompletion.return_value = _apply_response("HELLO WORLD")

        policy = ParallelRulesPolicy(config={"rules": [{"name": "uppercase", "instruction": "Uppercase"}]})
        ctx = PolicyContext.for_testing()
        result = await policy.simple_on_response_content("Hello world", ctx)
        assert result == "HELLO WORLD"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_single_rule_skips(self, mock_acompletion):
        """Rule decides it doesn't apply — content passes through."""
        mock_acompletion.return_value = _skip_response()

        policy = ParallelRulesPolicy(config={"rules": [{"name": "noop", "instruction": "Do nothing"}]})
        ctx = PolicyContext.for_testing()
        result = await policy.simple_on_response_content("Hello world", ctx)
        assert result == "Hello world"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_multiple_rules_all_skip(self, mock_acompletion):
        mock_acompletion.return_value = _skip_response()

        policy = ParallelRulesPolicy(
            config={"rules": [{"name": "r1", "instruction": "R1"}, {"name": "r2", "instruction": "R2"}]}
        )
        ctx = PolicyContext.for_testing()
        result = await policy.simple_on_response_content("Hello", ctx)
        assert result == "Hello"
        assert mock_acompletion.call_count == 2

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_multiple_rules_one_applies(self, mock_acompletion):
        """When 1 of N rules applies, use its version."""
        call_count = 0

        def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _skip_response()
            return _apply_response("CHANGED")

        mock_acompletion.side_effect = side_effect

        policy = ParallelRulesPolicy(
            config={"rules": [{"name": "r1", "instruction": "Skip"}, {"name": "r2", "instruction": "Apply"}]}
        )
        ctx = PolicyContext.for_testing()
        result = await policy.simple_on_response_content("Hello", ctx)
        assert result == "CHANGED"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_multiple_rules_trigger_refinement(self, mock_acompletion):
        """When 2+ rules apply, refinement merges them."""
        call_count = 0

        def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            messages = kwargs.get("messages", [])
            system_content = messages[0]["content"] if messages else ""

            if "text editor merging" in system_content:
                return _make_litellm_response("MERGED RESULT")
            elif call_count == 1:
                return _apply_response("VERSION A")
            else:
                return _apply_response("VERSION B")

        mock_acompletion.side_effect = side_effect

        policy = ParallelRulesPolicy(
            config={"rules": [{"name": "a", "instruction": "Apply A"}, {"name": "b", "instruction": "Apply B"}]}
        )
        ctx = PolicyContext.for_testing()
        result = await policy.simple_on_response_content("Hello", ctx)
        assert call_count == 3  # 2 rules + 1 refinement
        assert result == "MERGED RESULT"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_rule_failure_treated_as_skip(self, mock_acompletion):
        mock_acompletion.side_effect = Exception("LLM error")

        policy = ParallelRulesPolicy(config={"rules": [{"name": "failing", "instruction": "Fail"}]})
        ctx = PolicyContext.for_testing()
        result = await policy.simple_on_response_content("Hello", ctx)
        assert result == "Hello"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_unparseable_response_treated_as_skip(self, mock_acompletion):
        """If rule returns non-JSON, treat as skip."""
        mock_acompletion.return_value = _make_litellm_response("just some plain text")

        policy = ParallelRulesPolicy(config={"rules": [{"name": "bad", "instruction": "Bad output"}]})
        ctx = PolicyContext.for_testing()
        result = await policy.simple_on_response_content("Hello", ctx)
        assert result == "Hello"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_partial_failure_uses_successful_rule(self, mock_acompletion):
        def side_effect(**kwargs):
            if mock_acompletion.call_count == 1:
                raise Exception("Fail")
            return _apply_response("TRANSFORMED")

        mock_acompletion.side_effect = side_effect

        policy = ParallelRulesPolicy(
            config={"rules": [{"name": "fail", "instruction": "Fail"}, {"name": "ok", "instruction": "OK"}]}
        )
        ctx = PolicyContext.for_testing()
        result = await policy.simple_on_response_content("Hello", ctx)
        assert result == "TRANSFORMED"


# =============================================================================
# Dynamic rules
# =============================================================================


class TestDynamicRules:
    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_dynamic_rules_override_static(self, mock_acompletion):
        mock_acompletion.return_value = _apply_response("dynamic result")

        policy = ParallelRulesPolicy(config={"rules": [{"name": "static", "instruction": "Static"}]})
        ctx = PolicyContext.for_testing()
        policy.set_rules_for_request(ctx, [Rule(name="dynamic", instruction="Dynamic")])
        result = await policy.simple_on_response_content("Hello", ctx)
        assert result == "dynamic result"
        assert mock_acompletion.call_count == 1


# =============================================================================
# Config / credentials
# =============================================================================


class TestConfigWithApiCredentials:
    def test_config_with_api_credentials(self):
        config = ParallelRulesConfig(model="custom", api_base="https://api.custom.com", api_key="sk-test")
        assert config.api_base == "https://api.custom.com"
        assert config.api_key == "sk-test"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_apply_rule_uses_api_credentials(self, mock_acompletion):
        mock_acompletion.return_value = _skip_response()

        policy = ParallelRulesPolicy(
            config={"api_base": "https://api.custom.com", "api_key": "sk-test", "rules": [{"name": "t", "instruction": "T"}]}
        )
        ctx = PolicyContext.for_testing()
        await policy.simple_on_response_content("text", ctx)

        call_kwargs = mock_acompletion.call_args.kwargs
        assert call_kwargs.get("api_base") == "https://api.custom.com"
        assert call_kwargs.get("api_key") == "sk-test"


# =============================================================================
# Refinement failure
# =============================================================================


class TestRefinementFailureFallback:
    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_refine_failure_uses_first_result(self, mock_acompletion):
        call_count = 0

        async def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            messages = kwargs.get("messages", [])
            system_content = messages[0]["content"] if messages else ""

            if "text editor merging" in system_content:
                raise RuntimeError("Refinement failed")
            elif call_count == 1:
                return _apply_response("VERSION A")
            else:
                return _apply_response("VERSION B")

        mock_acompletion.side_effect = side_effect

        policy = ParallelRulesPolicy(
            config={"rules": [{"name": "a", "instruction": "A"}, {"name": "b", "instruction": "B"}]}
        )
        ctx = PolicyContext.for_testing()
        result = await policy.simple_on_response_content("original", ctx)
        assert result == "VERSION A"


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
