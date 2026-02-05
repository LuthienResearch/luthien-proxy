"""Unit tests for policy discovery module."""

from __future__ import annotations

import inspect
from typing import Any

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
        assert "ParallelRulesPolicy" in policy_names
        assert "SimpleJudgePolicy" in policy_names
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

    def test_simple_judge_schema(self) -> None:
        """Verify SimpleJudgePolicy has expected config schema."""
        policies = discover_policies()

        simple_judge = next(p for p in policies if p["name"] == "SimpleJudgePolicy")

        # Should have judge config params
        assert "judge_model" in simple_judge["config_schema"]
        assert "judge_temperature" in simple_judge["config_schema"]
        assert "block_threshold" in simple_judge["config_schema"]

    def test_parallel_rules_schema(self) -> None:
        """Verify ParallelRulesPolicy has expected config schema."""
        policies = discover_policies()

        parallel_rules = next(p for p in policies if p["name"] == "ParallelRulesPolicy")

        # Should have judge and rules params
        assert "judge" in parallel_rules["config_schema"]
        assert "rules" in parallel_rules["config_schema"]

        # Both should be object/array types (complex)
        assert parallel_rules["config_schema"]["judge"]["type"] == "object"
        assert parallel_rules["config_schema"]["rules"]["type"] == "array"

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


class TestSkipModules:
    """Tests for module filtering."""

    def test_skip_modules_contains_expected(self) -> None:
        assert "__init__" in SKIP_MODULES
        assert "base_policy" in SKIP_MODULES
        assert "simple_policy" in SKIP_MODULES

    def test_skip_suffixes(self) -> None:
        assert "_config" in SKIP_SUFFIXES
        assert "_utils" in SKIP_SUFFIXES
