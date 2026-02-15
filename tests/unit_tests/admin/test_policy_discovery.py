"""Unit tests for policy discovery module."""

from __future__ import annotations

import inspect
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from luthien_proxy.admin.policy_discovery import (
    SKIP_MODULES,
    SKIP_SUFFIXES,
    discover_policies,
    extract_config_schema,
    extract_description,
    python_type_to_json_schema,
)


class TestPythonTypeToJsonSchema:
    """Tests for python_type_to_json_schema function."""

    def test_str_type(self) -> None:
        result = python_type_to_json_schema(str)
        assert result == {"type": "string"}

    def test_int_type(self) -> None:
        result = python_type_to_json_schema(int)
        assert result == {"type": "integer"}

    def test_float_type(self) -> None:
        result = python_type_to_json_schema(float)
        assert result == {"type": "number"}

    def test_bool_type(self) -> None:
        result = python_type_to_json_schema(bool)
        assert result == {"type": "boolean"}

    def test_list_bare(self) -> None:
        result = python_type_to_json_schema(list)
        assert result == {"type": "array"}

    def test_dict_bare(self) -> None:
        result = python_type_to_json_schema(dict)
        assert result == {"type": "object", "additionalProperties": True}

    def test_list_parameterized(self) -> None:
        result = python_type_to_json_schema(list[str])
        assert result == {"type": "array", "items": {"type": "string"}}

    def test_list_nested(self) -> None:
        result = python_type_to_json_schema(list[dict[str, Any]])
        assert result == {
            "type": "array",
            "items": {"type": "object", "additionalProperties": True},
        }

    def test_dict_parameterized(self) -> None:
        result = python_type_to_json_schema(dict[str, Any])
        assert result == {"type": "object", "additionalProperties": True}

    def test_optional_str(self) -> None:
        result = python_type_to_json_schema(str | None)
        assert result == {"type": "string", "nullable": True}

    def test_optional_int(self) -> None:
        result = python_type_to_json_schema(int | None)
        assert result == {"type": "integer", "nullable": True}

    def test_empty_annotation(self) -> None:
        result = python_type_to_json_schema(inspect.Parameter.empty)
        assert result == {"type": "string"}

    def test_pydantic_model_schema_extraction(self) -> None:
        """Pydantic models should produce full JSON Schema with constraints."""

        class SampleConfig(BaseModel):
            """A sample config for testing."""

            name: str = Field(description="The name")
            temperature: float = Field(default=0.5, ge=0, le=2)
            api_key: str | None = Field(default=None, json_schema_extra={"format": "password"})

        schema = python_type_to_json_schema(SampleConfig)

        assert schema["type"] == "object"
        assert "properties" in schema

        # Check name field has description
        assert schema["properties"]["name"]["type"] == "string"
        assert schema["properties"]["name"]["description"] == "The name"

        # Check temperature has constraints
        temp = schema["properties"]["temperature"]
        assert temp["type"] == "number"
        assert temp["minimum"] == 0
        assert temp["maximum"] == 2
        assert temp["default"] == 0.5

        # Check api_key has password format
        key = schema["properties"]["api_key"]
        assert key.get("format") == "password"

    def test_discriminated_union_schema(self) -> None:
        """Discriminated unions should include oneOf with discriminator info."""

        class RegexRule(BaseModel):
            type: Literal["regex"] = "regex"
            pattern: str

        class KeywordRule(BaseModel):
            type: Literal["keyword"] = "keyword"
            keywords: list[str]

        RuleUnion = Annotated[RegexRule | KeywordRule, Field(discriminator="type")]

        schema = python_type_to_json_schema(RuleUnion)

        # Should have oneOf structure
        assert "oneOf" in schema or "anyOf" in schema
        variants = schema.get("oneOf") or schema.get("anyOf")
        assert len(variants) == 2

        # Should have discriminator metadata
        assert "discriminator" in schema
        assert schema["discriminator"]["propertyName"] == "type"


class TestExtractDescription:
    """Tests for extract_description function."""

    def test_class_with_docstring(self) -> None:
        class TestPolicy:
            """This is a test policy.

            It has multiple paragraphs.
            """

        result = extract_description(TestPolicy)
        assert result == "This is a test policy."

    def test_class_with_multiline_first_paragraph(self) -> None:
        class TestPolicy:
            """This is a test policy
            that spans multiple lines.

            Second paragraph.
            """

        result = extract_description(TestPolicy)
        assert result == "This is a test policy that spans multiple lines."

    def test_class_without_docstring(self) -> None:
        class TestPolicy:
            pass

        result = extract_description(TestPolicy)
        assert result == ""


class TestExtractConfigSchema:
    """Tests for extract_config_schema function."""

    def test_no_params(self) -> None:
        class TestPolicy:
            def __init__(self) -> None:
                pass

        config_schema, example_config = extract_config_schema(TestPolicy)
        assert config_schema == {}
        assert example_config == {}

    def test_params_with_defaults(self) -> None:
        class TestPolicy:
            def __init__(
                self,
                model: str = "default-model",
                temperature: float = 0.5,
            ) -> None:
                pass

        config_schema, example_config = extract_config_schema(TestPolicy)

        assert "model" in config_schema
        assert config_schema["model"]["type"] == "string"
        assert config_schema["model"]["default"] == "default-model"

        assert "temperature" in config_schema
        assert config_schema["temperature"]["type"] == "number"
        assert config_schema["temperature"]["default"] == 0.5

        assert example_config == {"model": "default-model", "temperature": 0.5}

    def test_optional_params(self) -> None:
        class TestPolicy:
            def __init__(
                self,
                api_key: str | None = None,
            ) -> None:
                pass

        config_schema, example_config = extract_config_schema(TestPolicy)

        assert config_schema["api_key"]["type"] == "string"
        assert config_schema["api_key"]["nullable"] is True
        assert config_schema["api_key"]["default"] is None

    def test_required_params(self) -> None:
        class TestPolicy:
            def __init__(
                self,
                required_param: str,
            ) -> None:
                pass

        config_schema, example_config = extract_config_schema(TestPolicy)

        assert "required_param" in config_schema
        assert config_schema["required_param"]["type"] == "string"
        assert "default" not in config_schema["required_param"]
        # Example should have placeholder
        assert example_config["required_param"] == ""

    def test_complex_params(self) -> None:
        class TestPolicy:
            def __init__(
                self,
                rules: list[dict[str, Any]] | None = None,
            ) -> None:
                pass

        config_schema, example_config = extract_config_schema(TestPolicy)

        assert config_schema["rules"]["type"] == "array"
        assert config_schema["rules"]["nullable"] is True


class TestDiscoverPolicies:
    """Tests for discover_policies function."""

    def test_discovers_known_policies(self) -> None:
        """Verify discovery finds expected policies."""
        policies = discover_policies()

        policy_names = [p["name"] for p in policies]

        # Should find these concrete policies
        assert "NoOpPolicy" in policy_names
        assert "AllCapsPolicy" in policy_names
        assert "ToolCallJudgePolicy" in policy_names

        # Should NOT include base classes
        assert "BasePolicy" not in policy_names
        assert "SimplePolicy" not in policy_names

    def test_policy_has_required_fields(self) -> None:
        """Verify each discovered policy has required fields."""
        policies = discover_policies()

        for policy in policies:
            assert "name" in policy
            assert "class_ref" in policy
            assert "description" in policy
            assert "config_schema" in policy
            assert "example_config" in policy

    def test_class_ref_format(self) -> None:
        """Verify class_ref is in module:ClassName format."""
        policies = discover_policies()

        for policy in policies:
            class_ref = policy["class_ref"]
            assert ":" in class_ref
            module, class_name = class_ref.rsplit(":", 1)
            assert module.startswith("luthien_proxy.policies.")
            assert class_name == policy["name"]

    def test_noop_policy_empty_config(self) -> None:
        """Verify NoOpPolicy has empty config schema."""
        policies = discover_policies()

        noop = next(p for p in policies if p["name"] == "NoOpPolicy")

        assert noop["config_schema"] == {}
        assert noop["example_config"] == {}

    def test_policies_sorted_by_name(self) -> None:
        """Verify policies are returned in alphabetical order."""
        policies = discover_policies()
        names = [p["name"] for p in policies]
        assert names == sorted(names)


# Module-level test classes for $defs aggregation tests
# Defined at module level so get_type_hints() can resolve them
class _NestedConfig(BaseModel):
    """Nested config for testing schema extraction."""

    value: int = 0


class _ParentConfig(BaseModel):
    """Parent config with nested model for testing."""

    nested: _NestedConfig
    name: str = "default"


class _FakePolicyWithNestedConfig:
    """A fake policy for testing schema extraction with nested Pydantic models."""

    def __init__(self, config: _ParentConfig, enabled: bool = True) -> None:
        self.config = config
        self.enabled = enabled


class TestDefsAggregation:
    """Tests for $defs aggregation in config schemas with nested Pydantic models."""

    def test_extract_config_schema_with_defs(self) -> None:
        """Config schema should include $defs for nested Pydantic models."""
        schema, example = extract_config_schema(_FakePolicyWithNestedConfig)

        # Should have config parameter with nested structure
        assert "config" in schema
        config_schema = schema["config"]

        # Should have $defs at top level or within config schema
        assert "$defs" in config_schema or "definitions" in config_schema
        defs = config_schema.get("$defs") or config_schema.get("definitions", {})
        assert "_NestedConfig" in defs


class TestSubPolicyListMarker:
    """Tests for x-sub-policy-list marker in config schemas."""

    def test_multi_serial_policy_has_marker(self) -> None:
        """MultiSerialPolicy's policies param should have x-sub-policy-list marker."""
        from luthien_proxy.policies.multi_serial_policy import MultiSerialPolicy

        schema, _ = extract_config_schema(MultiSerialPolicy)
        assert "policies" in schema
        assert schema["policies"].get("x-sub-policy-list") is True
        assert schema["policies"]["type"] == "array"

    def test_multi_parallel_policy_has_marker(self) -> None:
        """MultiParallelPolicy's policies param should have x-sub-policy-list marker."""
        from luthien_proxy.policies.multi_parallel_policy import MultiParallelPolicy

        schema, _ = extract_config_schema(MultiParallelPolicy)
        assert "policies" in schema
        assert schema["policies"].get("x-sub-policy-list") is True
        assert schema["policies"]["type"] == "array"

    def test_multi_parallel_other_params_correct(self) -> None:
        """MultiParallelPolicy's other params should be extracted correctly."""
        from luthien_proxy.policies.multi_parallel_policy import MultiParallelPolicy

        schema, example = extract_config_schema(MultiParallelPolicy)

        assert "consolidation_strategy" in schema
        assert schema["consolidation_strategy"]["type"] == "string"
        assert schema["consolidation_strategy"]["default"] == "first_block"

        assert "designated_policy_index" in schema
        assert schema["designated_policy_index"]["type"] == "integer"
        assert schema["designated_policy_index"]["nullable"] is True

    def test_string_annotation_resolved_not_fallback(self) -> None:
        """String annotations from __future__ should resolve to proper types."""
        from luthien_proxy.policies.multi_serial_policy import MultiSerialPolicy

        schema, _ = extract_config_schema(MultiSerialPolicy)
        # Should be a proper array type, not the string fallback
        assert schema["policies"]["type"] == "array"
        assert "Python type" not in schema["policies"].get("description", "")

    def test_regular_list_no_marker(self) -> None:
        """Regular list params should NOT have x-sub-policy-list marker."""

        class TestPolicy:
            def __init__(self, items: list[str]) -> None:
                pass

        schema, _ = extract_config_schema(TestPolicy)
        assert "items" in schema
        assert schema["items"]["type"] == "array"
        assert "x-sub-policy-list" not in schema["items"]

    def test_non_policies_name_no_marker(self) -> None:
        """list[dict[str, Any]] with a different param name should NOT have marker."""

        class TestPolicy:
            def __init__(self, rules: list[dict[str, Any]]) -> None:
                pass

        schema, _ = extract_config_schema(TestPolicy)
        assert "rules" in schema
        assert "x-sub-policy-list" not in schema["rules"]


class TestSkipModules:
    """Tests for module filtering."""

    def test_skip_modules_contains_expected(self) -> None:
        assert "__init__" in SKIP_MODULES
        assert "base_policy" in SKIP_MODULES
        assert "simple_policy" in SKIP_MODULES

    def test_skip_suffixes(self) -> None:
        assert "_config" in SKIP_SUFFIXES
        assert "_utils" in SKIP_SUFFIXES
