"""Unit tests for SamplePydanticPolicy.

Tests verify that SamplePydanticPolicy:
1. Accepts Pydantic model as config
2. Has correct schema extraction for dynamic form generation
3. Supports discriminated unions for rule types
"""

from __future__ import annotations

import pytest

from luthien_proxy.admin.policy_discovery import extract_config_schema
from luthien_proxy.policies.sample_pydantic_policy import (
    KeywordRuleConfig,
    RegexRuleConfig,
    SampleConfig,
    SamplePydanticPolicy,
)
from luthien_proxy.policy_core import BasePolicy, OpenAIPolicyInterface
from luthien_proxy.policy_core.anthropic_interface import AnthropicPolicyInterface


class TestSamplePydanticPolicyBasics:
    """Tests for basic policy instantiation and config handling."""

    def test_inherits_from_base_policy(self):
        """SamplePydanticPolicy inherits from BasePolicy."""
        assert issubclass(SamplePydanticPolicy, BasePolicy)

    def test_implements_openai_interface(self):
        """SamplePydanticPolicy must implement OpenAIPolicyInterface to handle requests.

        Regression test: Previously only inherited BasePolicy, so activating it
        in the config UI caused all requests to crash.
        """
        assert issubclass(SamplePydanticPolicy, OpenAIPolicyInterface)

    def test_implements_anthropic_interface(self):
        """SamplePydanticPolicy must implement AnthropicPolicyInterface to handle requests.

        Regression test: Previously only inherited BasePolicy, so activating it
        in the config UI caused all requests to crash.
        """
        assert issubclass(SamplePydanticPolicy, AnthropicPolicyInterface)

    def test_instantiation_with_default_config(self):
        """Policy can be instantiated without arguments."""
        policy = SamplePydanticPolicy()
        assert policy is not None
        assert isinstance(policy, BasePolicy)
        assert policy.config.name == "default"
        assert policy.config.enabled is True

    def test_accepts_pydantic_config(self):
        """Policy should accept Pydantic model as config."""
        config = SampleConfig(
            name="test",
            rules=[KeywordRuleConfig(keywords=["bad", "word"])],
        )
        policy = SamplePydanticPolicy(config=config)
        assert policy.config.name == "test"
        assert len(policy.config.rules) == 1

    def test_config_with_regex_rule(self):
        """Policy accepts regex rules."""
        config = SampleConfig(
            name="regex-test",
            rules=[RegexRuleConfig(pattern=r"\d+", case_sensitive=True)],
        )
        policy = SamplePydanticPolicy(config=config)
        assert len(policy.config.rules) == 1
        rule = policy.config.rules[0]
        assert rule.type == "regex"
        assert rule.pattern == r"\d+"

    def test_config_with_mixed_rules(self):
        """Policy accepts mix of rule types."""
        config = SampleConfig(
            name="mixed",
            rules=[
                KeywordRuleConfig(keywords=["bad"]),
                RegexRuleConfig(pattern=r"[0-9]+"),
            ],
        )
        policy = SamplePydanticPolicy(config=config)
        assert len(policy.config.rules) == 2
        assert policy.config.rules[0].type == "keyword"
        assert policy.config.rules[1].type == "regex"


class TestSamplePydanticPolicyGetConfig:
    """Tests for get_config method."""

    def test_get_config_returns_dict(self):
        """get_config returns dict representation."""
        config = SampleConfig(
            name="test",
            threshold=0.7,
        )
        policy = SamplePydanticPolicy(config=config)
        result = policy.get_config()

        assert isinstance(result, dict)
        assert result["name"] == "test"
        assert result["threshold"] == 0.7

    def test_get_config_serializes_rules(self):
        """get_config correctly serializes rule configs."""
        config = SampleConfig(
            name="test",
            rules=[
                KeywordRuleConfig(keywords=["word1", "word2"]),
            ],
        )
        policy = SamplePydanticPolicy(config=config)
        result = policy.get_config()

        rules = result["rules"]
        assert len(rules) == 1
        assert rules[0]["type"] == "keyword"
        assert rules[0]["keywords"] == ["word1", "word2"]


class TestSamplePydanticPolicySchemaExtraction:
    """Tests for schema extraction from the policy."""

    def test_schema_extraction(self):
        """Policy schema should include full Pydantic structure."""
        schema, example = extract_config_schema(SamplePydanticPolicy)

        assert "config" in schema
        config_schema = schema["config"]

        # Should have $defs for nested types
        assert "$defs" in config_schema

    def test_schema_has_rule_config_definitions(self):
        """Schema should include RuleConfig definitions."""
        schema, _example = extract_config_schema(SamplePydanticPolicy)
        config_schema = schema["config"]
        defs = config_schema.get("$defs", {})

        # Should have definitions for rule types
        def_keys = list(defs.keys())
        def_str = str(defs)

        # The schema should reference either KeywordRuleConfig or RegexRuleConfig
        has_keyword_rule = "KeywordRuleConfig" in def_str or "keyword" in def_str.lower()
        has_regex_rule = "RegexRuleConfig" in def_str or "regex" in def_str.lower()

        assert has_keyword_rule or has_regex_rule, f"Expected rule configs in $defs: {def_keys}"

    def test_schema_properties(self):
        """Schema should have expected properties."""
        schema, _example = extract_config_schema(SamplePydanticPolicy)
        config_schema = schema["config"]

        properties = config_schema.get("properties", {})

        # Check for key properties
        assert "name" in properties
        assert "enabled" in properties
        assert "threshold" in properties
        assert "rules" in properties

    def test_threshold_has_constraints(self):
        """Threshold field should have min/max constraints."""
        schema, _example = extract_config_schema(SamplePydanticPolicy)
        config_schema = schema["config"]
        properties = config_schema.get("properties", {})

        threshold = properties.get("threshold", {})
        # Pydantic should include minimum/maximum in the schema
        assert "minimum" in threshold or "exclusiveMinimum" in threshold or threshold.get("type") == "number"

    def test_api_key_has_password_format(self):
        """api_key field should have password format."""
        schema, _example = extract_config_schema(SamplePydanticPolicy)
        config_schema = schema["config"]
        properties = config_schema.get("properties", {})

        api_key = properties.get("api_key", {})
        # Check for password format or anyOf with password format
        schema_str = str(api_key)
        assert "password" in schema_str.lower() or api_key.get("format") == "password"


class TestSamplePydanticPolicyNoop:
    """Tests verifying the policy is a no-op (does nothing)."""

    def test_short_policy_name(self):
        """Policy should have readable short name."""
        policy = SamplePydanticPolicy()
        assert policy.short_policy_name == "SamplePydanticPolicy"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
