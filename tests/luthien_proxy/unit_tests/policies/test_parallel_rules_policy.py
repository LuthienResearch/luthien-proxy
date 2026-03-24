"""Unit tests for ParallelRulesPolicy."""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from litellm.types.utils import Choices, Message, ModelResponse

from conftest import DEFAULT_TEST_MODEL
from luthien_proxy.llm.types.anthropic import AnthropicResponse
from luthien_proxy.policies.parallel_rules_policy import (
    ParallelRulesConfig,
    ParallelRulesPolicy,
    _RuleResult,
)
from luthien_proxy.policies.simple_policy import SimplePolicy
from luthien_proxy.policy_core import AnthropicExecutionInterface
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.policies.parallel_rules_policy import Rule


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


class TestParallelRulesPolicyProtocol:
    """Tests verifying ParallelRulesPolicy implements required protocols."""

    def test_inherits_simple_policy(self):
        """ParallelRulesPolicy is a SimplePolicy."""
        policy = ParallelRulesPolicy()
        assert isinstance(policy, SimplePolicy)

    def test_implements_anthropic_interface(self):
        """ParallelRulesPolicy implements AnthropicExecutionInterface."""
        policy = ParallelRulesPolicy()
        assert isinstance(policy, AnthropicExecutionInterface)

    def test_short_policy_name(self):
        """short_policy_name returns 'ParallelRules'."""
        policy = ParallelRulesPolicy()
        assert policy.short_policy_name == "ParallelRules"


class TestParallelRulesConfig:
    """Tests for ParallelRulesConfig parsing and defaults."""

    def test_default_config(self):
        """Default config has sensible defaults."""
        config = ParallelRulesConfig()
        assert config.model == "claude-haiku-4-5"
        assert config.rules == []
        assert config.temperature == 0.0
        assert config.max_tokens == 4096

    def test_config_from_dict(self):
        """Config parses from dict."""
        config = ParallelRulesConfig(**{"model": "claude-opus", "temperature": 0.5})
        assert config.model == "claude-opus"
        assert config.temperature == 0.5

    def test_static_rules_parsed(self):
        """Static rules from config are converted to Rule objects."""
        policy = ParallelRulesPolicy(config={"rules": [{"name": "r1", "instruction": "Do thing 1"}]})
        assert len(policy._static_rules) == 1
        assert policy._static_rules[0].name == "r1"
        assert policy._static_rules[0].instruction == "Do thing 1"

    def test_multiple_static_rules(self):
        """Multiple static rules are all parsed."""
        policy = ParallelRulesPolicy(
            config={
                "rules": [
                    {"name": "r1", "instruction": "Rule 1"},
                    {"name": "r2", "instruction": "Rule 2"},
                ]
            }
        )
        assert len(policy._static_rules) == 2
        assert policy._static_rules[0].name == "r1"
        assert policy._static_rules[1].name == "r2"


class TestRuleApplication:
    """Tests for rule application logic."""

    @pytest.mark.asyncio
    async def test_no_rules_passthrough(self):
        """With no rules, content passes through unchanged."""
        policy = ParallelRulesPolicy()
        ctx = PolicyContext.for_testing()
        result = await policy.simple_on_response_content("Hello world", ctx)
        assert result == "Hello world"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_single_rule_applies(self, mock_acompletion):
        """When one rule changes the text, its version is used."""
        mock_acompletion.return_value = _make_litellm_response("HELLO WORLD")

        policy = ParallelRulesPolicy(config={"rules": [{"name": "uppercase", "instruction": "Convert to uppercase"}]})
        ctx = PolicyContext.for_testing()
        result = await policy.simple_on_response_content("Hello world", ctx)
        assert result == "HELLO WORLD"
        mock_acompletion.assert_called_once()

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_single_rule_no_change(self, mock_acompletion):
        """If rule returns text unchanged, passthrough."""
        mock_acompletion.return_value = _make_litellm_response("Hello world")

        policy = ParallelRulesPolicy(config={"rules": [{"name": "noop", "instruction": "Do nothing"}]})
        ctx = PolicyContext.for_testing()
        result = await policy.simple_on_response_content("Hello world", ctx)
        assert result == "Hello world"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_multiple_rules_no_changes(self, mock_acompletion):
        """When multiple rules don't change text, passthrough."""
        mock_acompletion.return_value = _make_litellm_response("Hello world")

        policy = ParallelRulesPolicy(
            config={
                "rules": [
                    {"name": "r1", "instruction": "Rule 1"},
                    {"name": "r2", "instruction": "Rule 2"},
                ]
            }
        )
        ctx = PolicyContext.for_testing()
        result = await policy.simple_on_response_content("Hello world", ctx)
        assert result == "Hello world"
        assert mock_acompletion.call_count == 2

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_multiple_rules_one_change_uses_that_version(self, mock_acompletion):
        """When 1 out of N rules changes text, use that version."""

        def side_effect(**kwargs):
            # Return same as input first, then changed on second
            return (
                _make_litellm_response("Hello world")
                if mock_acompletion.call_count == 1
                else _make_litellm_response("HELLO WORLD")
            )

        mock_acompletion.side_effect = side_effect

        policy = ParallelRulesPolicy(
            config={
                "rules": [
                    {"name": "r1", "instruction": "No change"},
                    {"name": "r2", "instruction": "Uppercase"},
                ]
            }
        )
        ctx = PolicyContext.for_testing()
        result = await policy.simple_on_response_content("Hello world", ctx)
        assert result == "HELLO WORLD"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_multiple_rules_multiple_changes_triggers_refinement(self, mock_acompletion):
        """When 2+ rules change content, a refinement call merges them."""
        call_count = 0

        def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            messages = kwargs.get("messages", [])
            system_content = messages[0]["content"] if messages else ""

            if "text editor merging" in system_content:
                # Refinement call
                return _make_litellm_response("HELLO WORLD (concise)")
            elif call_count == 1:
                return _make_litellm_response("HELLO WORLD")
            else:
                return _make_litellm_response("Hello world (concise)")

        mock_acompletion.side_effect = side_effect

        policy = ParallelRulesPolicy(
            config={
                "rules": [
                    {"name": "uppercase", "instruction": "Convert to uppercase"},
                    {"name": "concise", "instruction": "Make concise"},
                ]
            }
        )
        ctx = PolicyContext.for_testing()
        result = await policy.simple_on_response_content("Hello world", ctx)

        # Should have called: 2 rule applications + 1 refinement = 3 calls
        assert call_count == 3
        assert result == "HELLO WORLD (concise)"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_rule_failure_treated_as_no_change(self, mock_acompletion):
        """If a rule's LLM call fails, treat as no-change and continue."""
        mock_acompletion.side_effect = Exception("LLM error")

        policy = ParallelRulesPolicy(config={"rules": [{"name": "failing", "instruction": "Do thing"}]})
        ctx = PolicyContext.for_testing()
        result = await policy.simple_on_response_content("Hello world", ctx)
        assert result == "Hello world"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_partial_rule_failure_in_multiple_rules(self, mock_acompletion):
        """When one rule fails and another succeeds with change, use the success."""

        def side_effect(**kwargs):
            if mock_acompletion.call_count == 1:
                raise Exception("Rule failed")
            else:
                return _make_litellm_response("TRANSFORMED")

        mock_acompletion.side_effect = side_effect

        policy = ParallelRulesPolicy(
            config={
                "rules": [
                    {"name": "failing", "instruction": "Fail"},
                    {"name": "transform", "instruction": "Transform"},
                ]
            }
        )
        ctx = PolicyContext.for_testing()
        result = await policy.simple_on_response_content("Hello", ctx)
        assert result == "TRANSFORMED"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_refinement_respects_config_model(self, mock_acompletion):
        """Refinement call uses configured model."""
        mock_acompletion.return_value = _make_litellm_response("refined")

        policy = ParallelRulesPolicy(
            config={
                "model": "claude-opus",
                "rules": [
                    {"name": "r1", "instruction": "Rule 1"},
                    {"name": "r2", "instruction": "Rule 2"},
                ],
            }
        )
        ctx = PolicyContext.for_testing()
        await policy.simple_on_response_content("text", ctx)

        # Check that calls used the right model
        for call in mock_acompletion.call_args_list:
            assert call.kwargs["model"] == "claude-opus"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_refinement_includes_rule_names_and_instructions(self, mock_acompletion):
        """Refinement call includes rule names and instructions."""
        mock_acompletion.return_value = _make_litellm_response("refined")

        policy = ParallelRulesPolicy(
            config={
                "rules": [
                    {"name": "uppercase", "instruction": "Make uppercase"},
                    {"name": "concise", "instruction": "Make concise"},
                ]
            }
        )
        ctx = PolicyContext.for_testing()
        await policy.simple_on_response_content("hello", ctx)

        # Find the refinement call (the one with "text editor merging")
        refinement_call = None
        for call in mock_acompletion.call_args_list:
            messages = call.kwargs.get("messages", [])
            system = messages[0]["content"] if messages else ""
            if "text editor merging" in system:
                refinement_call = call
                break

        assert refinement_call is not None
        # Check user message contains rule names
        user_msg = refinement_call.kwargs["messages"][1]["content"]
        assert "uppercase" in user_msg
        assert "concise" in user_msg


class TestDynamicRules:
    """Tests for dynamic rules via request state."""

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_dynamic_rules_override_static(self, mock_acompletion):
        """Dynamic rules set via set_rules_for_request take precedence."""
        mock_acompletion.return_value = _make_litellm_response("dynamic result")

        policy = ParallelRulesPolicy(config={"rules": [{"name": "static", "instruction": "Static rule"}]})
        ctx = PolicyContext.for_testing()
        policy.set_rules_for_request(ctx, [Rule(name="dynamic", instruction="Dynamic rule")])
        result = await policy.simple_on_response_content("Hello", ctx)

        # Should use the dynamic rule
        assert result == "dynamic result"
        # Only one call (one dynamic rule, not the static one)
        assert mock_acompletion.call_count == 1

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_empty_dynamic_rules_fallback_to_static(self, mock_acompletion):
        """If dynamic rules are empty list, fall back to static."""
        mock_acompletion.return_value = _make_litellm_response("static result")

        policy = ParallelRulesPolicy(config={"rules": [{"name": "static", "instruction": "Static rule"}]})
        ctx = PolicyContext.for_testing()
        policy.set_rules_for_request(ctx, [])
        result = await policy.simple_on_response_content("Hello", ctx)

        assert result == "static result"
        # Should use static rules (1 rule)
        assert mock_acompletion.call_count == 1

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_multiple_dynamic_rules(self, mock_acompletion):
        """Multiple dynamic rules are applied in parallel."""

        def side_effect(**kwargs):
            messages = kwargs.get("messages", [])
            system = messages[0]["content"] if messages else ""
            if "text editor merging" in system:
                return _make_litellm_response("merged")
            else:
                return _make_litellm_response(f"result_{mock_acompletion.call_count}")

        mock_acompletion.side_effect = side_effect

        policy = ParallelRulesPolicy()
        ctx = PolicyContext.for_testing()
        policy.set_rules_for_request(
            ctx,
            [
                Rule(name="r1", instruction="Rule 1"),
                Rule(name="r2", instruction="Rule 2"),
            ],
        )
        await policy.simple_on_response_content("Hello", ctx)

        # Should trigger refinement (2 rules that change)
        assert mock_acompletion.call_count == 3


class TestRuleResultDataclass:
    """Tests for _RuleResult dataclass."""

    def test_rule_result_unchanged(self):
        """_RuleResult.changed is False when text unchanged."""
        rule = Rule(name="test", instruction="Do thing")
        result = _RuleResult(rule=rule, rewritten="hello", changed=False)
        assert result.changed is False
        assert result.rule.name == "test"
        assert result.rewritten == "hello"

    def test_rule_result_changed(self):
        """_RuleResult.changed is True when text changed."""
        rule = Rule(name="test", instruction="Do thing")
        result = _RuleResult(rule=rule, rewritten="HELLO", changed=True)
        assert result.changed is True


class TestNonStreamingAnthropicPath:
    """Tests for the full non-streaming Anthropic execution path."""

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_run_anthropic_applies_rules(self, mock_acompletion):
        """Full run_anthropic path applies rules to response content."""
        mock_acompletion.return_value = _make_litellm_response("TRANSFORMED")

        policy = ParallelRulesPolicy(config={"rules": [{"name": "transform", "instruction": "Transform it"}]})
        ctx = PolicyContext.for_testing()

        # Create a mock IO object
        mock_io = MagicMock()
        mock_io.request = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "test"}],
            "max_tokens": 100,
        }
        mock_io.first_backend_response = None

        mock_response: AnthropicResponse = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Original text"}],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        async def mock_complete(request):
            return mock_response

        mock_io.complete = mock_complete

        emissions = []
        async for emission in policy.run_anthropic(mock_io, ctx):
            emissions.append(emission)

        assert len(emissions) == 1
        response = emissions[0]
        assert isinstance(response, dict)
        text_block = cast(dict, response["content"][0])
        assert text_block["text"] == "TRANSFORMED"

    @pytest.mark.asyncio
    async def test_run_anthropic_with_no_rules(self):
        """run_anthropic passes through unchanged when no rules."""
        policy = ParallelRulesPolicy()
        ctx = PolicyContext.for_testing()

        mock_io = MagicMock()
        mock_io.request = {
            "model": DEFAULT_TEST_MODEL,
            "messages": [{"role": "user", "content": "test"}],
        }
        mock_io.first_backend_response = None

        mock_response: AnthropicResponse = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Original text"}],
            "model": DEFAULT_TEST_MODEL,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

        async def mock_complete(request):
            return mock_response

        mock_io.complete = mock_complete

        emissions = []
        async for emission in policy.run_anthropic(mock_io, ctx):
            emissions.append(emission)

        assert len(emissions) == 1
        text_block = cast(dict, emissions[0]["content"][0])
        assert text_block["text"] == "Original text"


class TestConfigWithApiCredentials:
    """Tests for config with optional API credentials."""

    def test_config_with_api_credentials(self):
        """Config accepts and stores API credentials."""
        config = ParallelRulesConfig(
            model="custom-model",
            api_base="https://api.custom.com",
            api_key="sk-test",
        )
        assert config.api_base == "https://api.custom.com"
        assert config.api_key == "sk-test"

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_apply_rule_uses_api_credentials(self, mock_acompletion):
        """_apply_rule passes API credentials to acompletion."""
        mock_acompletion.return_value = _make_litellm_response("result")

        policy = ParallelRulesPolicy(
            config={
                "api_base": "https://api.custom.com",
                "api_key": "sk-test",
                "rules": [{"name": "test", "instruction": "Do it"}],
            }
        )
        ctx = PolicyContext.for_testing()
        await policy.simple_on_response_content("text", ctx)

        # Check that API credentials were passed
        call_kwargs = mock_acompletion.call_args.kwargs
        assert call_kwargs.get("api_base") == "https://api.custom.com"
        assert call_kwargs.get("api_key") == "sk-test"


class TestRefinementFailureFallback:
    """Test that refinement failure falls back to first changed result."""

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
                raise RuntimeError("LLM refinement failed")
            elif call_count == 1:
                return _make_litellm_response("VERSION A")
            else:
                return _make_litellm_response("VERSION B")

        mock_acompletion.side_effect = side_effect

        policy = ParallelRulesPolicy(
            config={
                "rules": [
                    {"name": "rule-a", "instruction": "Apply A"},
                    {"name": "rule-b", "instruction": "Apply B"},
                ]
            }
        )
        ctx = PolicyContext.for_testing()
        result = await policy.simple_on_response_content("original", ctx)

        # Should fall back to first changed result when refinement fails
        assert result == "VERSION A"


class TestMaxRules:
    """Test that max_rules config caps the number of applied rules."""

    @pytest.mark.asyncio
    @patch("luthien_proxy.policies.rules_llm_utils.acompletion")
    async def test_max_rules_truncates(self, mock_acompletion):
        # Return unchanged text so no refinement call is triggered
        mock_acompletion.return_value = _make_litellm_response("text")

        policy = ParallelRulesPolicy(
            config={
                "max_rules": 2,
                "rules": [
                    {"name": "r1", "instruction": "Rule 1"},
                    {"name": "r2", "instruction": "Rule 2"},
                    {"name": "r3", "instruction": "Rule 3"},
                ],
            }
        )
        ctx = PolicyContext.for_testing()
        await policy.simple_on_response_content("text", ctx)

        # Only 2 rule calls (max_rules=2), not 3
        assert mock_acompletion.call_count == 2
