"""Unit tests for policy type registry functions.

Tests cover:
1. derive_builtin_name converts class names to kebab-case identifiers
2. resolve_collisions assigns unique names, handling collisions deterministically
3. _resolve_description extracts descriptions from attributes or docstrings
4. REGISTERED_BUILTINS has no name collisions
"""

from __future__ import annotations

import re

import pytest

from luthien_proxy.policy_types import (
    REGISTERED_BUILTINS,
    _resolve_description,
    derive_builtin_name,
    resolve_collisions,
)


class TestDeriveBuiltinName:
    """Test convert class name to kebab-case identifier."""

    @pytest.mark.parametrize(
        "class_name,expected",
        [
            ("SimpleLLMPolicy", "simple-llm"),
            ("NoOpPolicy", "no-op"),
            ("AllCapsPolicy", "all-caps"),
            ("TextModifierPolicy", "text-modifier"),
            ("LLMPolicy", "llm"),
            ("HTTPSPolicy", "https"),
        ],
    )
    def test_converts_pascal_to_kebab(self, class_name: str, expected: str) -> None:
        """Policy suffix dropped, camelCase converted to kebab, lowercased."""
        assert derive_builtin_name(class_name) == expected

    def test_no_policy_suffix_unchanged(self) -> None:
        """Class name without Policy suffix stays as-is (after camelCase conversion)."""
        assert derive_builtin_name("PolicyManager") == "policy-manager"

    def test_only_policy_suffix(self) -> None:
        """Single word ending in Policy drops the suffix."""
        assert derive_builtin_name("OnlyPolicy") == "only"


class TestResolveCollisions:
    """Test deterministic collision resolution."""

    def test_no_collisions_returns_input_names(self) -> None:
        """When no collisions, each class gets its derived name in input order."""
        discovered = [
            {"name": "SimpleLLMPolicy", "class_ref": "luthien_proxy.policies.simple_llm:SimpleLLMPolicy"},
            {"name": "NoOpPolicy", "class_ref": "luthien_proxy.policies.noop:NoOpPolicy"},
        ]
        result = resolve_collisions(discovered)

        assert len(result) == 2
        assert result[0][0] == "simple-llm"
        assert result[0][1] is discovered[0]
        assert result[1][0] == "no-op"
        assert result[1][1] is discovered[1]

    def test_appends_suffix_in_module_path_order(self) -> None:
        """When two classes derive same name, lexicographically lower module_path wins."""
        discovered = [
            {"name": "SimplePolicy", "class_ref": "z_module:SimplePolicy"},
            {"name": "SimplePolicy", "class_ref": "a_module:SimplePolicy"},
        ]
        result = resolve_collisions(discovered)

        # Sort order by class_ref: a_module < z_module
        # a_module wins bare name "simple"; z_module gets "simple-2"
        # Result is in INPUT order
        assert result[0][0] == "simple-2"  # z_module is first in input, second in sort
        assert result[1][0] == "simple"  # a_module is second in input, first in sort

    def test_handles_three_way_collision(self) -> None:
        """Three classes deriving same name get bare, -2, -3 by module_path order."""
        discovered = [
            {"name": "SimplePolicy", "class_ref": "zz.module:SimplePolicy"},
            {"name": "SimplePolicy", "class_ref": "aa.module:SimplePolicy"},
            {"name": "SimplePolicy", "class_ref": "mm.module:SimplePolicy"},
        ]
        result = resolve_collisions(discovered)

        # Sort order: aa < mm < zz → aa gets "simple", mm gets "simple-2", zz gets "simple-3"
        # Result in input order: zz first, aa second, mm third
        names_in_input_order = [name for name, _ in result]
        assert names_in_input_order == ["simple-3", "simple", "simple-2"]

    def test_is_stable_across_runs(self) -> None:
        """Same input produces identical output when called twice."""
        discovered = [
            {"name": "FooPolicy", "class_ref": "c.module:FooPolicy"},
            {"name": "BarPolicy", "class_ref": "a.module:BarPolicy"},
            {"name": "BazPolicy", "class_ref": "b.module:BazPolicy"},
            {"name": "BarPolicy", "class_ref": "d.module:BarPolicy"},
        ]

        names1 = [name for name, _ in resolve_collisions(discovered)]
        names2 = [name for name, _ in resolve_collisions(discovered)]

        assert names1 == names2, "resolve_collisions should be deterministic"

    def test_mixed_collisions_and_non_collisions(self) -> None:
        """Some classes collide, others don't."""
        discovered = [
            {"name": "UniquePolicy", "class_ref": "unique.module:UniquePolicy"},
            {"name": "DupPolicy", "class_ref": "zz.module:DupPolicy"},
            {"name": "DupPolicy", "class_ref": "aa.module:DupPolicy"},
        ]
        result = resolve_collisions(discovered)

        names = [name for name, _ in result]
        assert sorted(names) == sorted(["unique", "dup", "dup-2"])


class TestResolveDescription:
    """Test description extraction from class definitions."""

    def test_prefers_attribute_over_docstring(self) -> None:
        """Class with both __policy_description__ and docstring → attribute wins."""

        class PolicyWithBoth:
            __policy_description__ = "From attribute"

            """From docstring"""

        result = _resolve_description(PolicyWithBoth)
        assert result == "From attribute"

    def test_falls_back_to_docstring(self) -> None:
        """Class with only a docstring."""

        class PolicyWithDocstring:
            """From docstring"""

        result = _resolve_description(PolicyWithDocstring)
        assert result == "From docstring"

    def test_returns_none_when_neither(self) -> None:
        """Bare class, no docstring, no attribute → None."""

        class BarePolicy:
            pass

        result = _resolve_description(BarePolicy)
        assert result is None

    def test_empty_docstring_returns_none(self) -> None:
        """Class with empty docstring string → None."""

        class PolicyWithEmptyDocstring:
            """"""

        result = _resolve_description(PolicyWithEmptyDocstring)
        assert result is None


def test_registered_builtins_have_no_name_collisions() -> None:
    """REGISTERED_BUILTINS entries should have no name collisions."""
    discovered = [{"name": class_ref.split(":", 1)[1], "class_ref": class_ref} for class_ref in REGISTERED_BUILTINS]
    result = resolve_collisions(discovered)

    suffix_pattern = re.compile(r"-\d+$")
    for name, _ in result:
        assert not suffix_pattern.search(name), f"Collision in REGISTERED_BUILTINS: {name} has numeric suffix"
