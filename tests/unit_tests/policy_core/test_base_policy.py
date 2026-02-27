# ABOUTME: Unit tests for BasePolicy class

"""Unit tests for BasePolicy class."""

import pytest

from luthien_proxy.policy_core.base_policy import BasePolicy


class _MutableStatePolicy(BasePolicy):
    def __init__(self) -> None:
        self.buffer: list[str] = []


class _AllowlistedMutablePolicy(BasePolicy):
    _ALLOWED_MUTABLE_INSTANCE_ATTRS: frozenset[str] = frozenset({"buffer"})

    def __init__(self) -> None:
        self.buffer: dict[str, str] = {}
        self.label = "config"


class TestBasePolicy:
    """Tests for BasePolicy class."""

    def test_short_policy_name_returns_class_name(self):
        """short_policy_name should return the class name by default."""
        policy = BasePolicy()
        assert policy.short_policy_name == "BasePolicy"

    def test_short_policy_name_returns_subclass_name(self):
        """short_policy_name should return the subclass name."""

        class MyCustomPolicy(BasePolicy):
            pass

        policy = MyCustomPolicy()
        assert policy.short_policy_name == "MyCustomPolicy"

    def test_short_policy_name_can_be_overridden(self):
        """Subclasses can override short_policy_name."""

        class MyPolicy(BasePolicy):
            @property
            def short_policy_name(self) -> str:
                return "CustomName"

        policy = MyPolicy()
        assert policy.short_policy_name == "CustomName"

    def test_base_policy_is_instantiable(self):
        """BasePolicy should be directly instantiable (not abstract)."""
        policy = BasePolicy()
        assert isinstance(policy, BasePolicy)

    def test_freeze_configured_state_blocks_post_freeze_assignment(self):
        """Policies should reject instance mutation after freeze."""
        policy = BasePolicy()
        policy.configured_value = "ok"
        policy.freeze_configured_state()

        with pytest.raises(AttributeError, match="frozen after configuration"):
            policy.runtime_value = "nope"

    def test_freeze_configured_state_rejects_mutable_instance_containers(self):
        """Policies with mutable runtime-like containers should fail freeze."""
        policy = _MutableStatePolicy()

        with pytest.raises(TypeError, match="mutable container"):
            policy.freeze_configured_state()

    def test_freeze_allows_explicitly_allowlisted_mutable_attrs(self):
        """Allowlisted mutable attrs are permitted during freeze validation."""
        policy = _AllowlistedMutablePolicy()

        policy.freeze_configured_state()
        assert policy.label == "config"
