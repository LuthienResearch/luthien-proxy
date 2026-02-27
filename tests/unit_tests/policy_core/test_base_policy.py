# ABOUTME: Unit tests for BasePolicy class

"""Unit tests for BasePolicy class."""

import pytest

from luthien_proxy.policy_core.base_policy import BasePolicy


class _MutableStatePolicy(BasePolicy):
    def __init__(self) -> None:
        self.buffer: list[str] = []


class _PrivateMutablePolicy(BasePolicy):
    def __init__(self) -> None:
        self._buffer: dict[str, str] = {}
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

    def test_freeze_configured_state_does_not_freeze_attribute_assignment(self):
        """freeze_configured_state validates but does not freeze runtime assignment."""
        policy = BasePolicy()
        policy.configured_value = "ok"
        policy.freeze_configured_state()
        policy.runtime_value = "allowed"
        assert policy.runtime_value == "allowed"

    def test_freeze_configured_state_rejects_mutable_instance_containers(self):
        """Policies with mutable runtime-like containers should fail freeze."""
        policy = _MutableStatePolicy()

        with pytest.raises(TypeError, match="mutable container"):
            policy.freeze_configured_state()

    def test_freeze_ignores_private_mutable_attrs(self):
        """Private mutable attrs are internal details and ignored by validation."""
        policy = _PrivateMutablePolicy()

        policy.freeze_configured_state()
        assert policy.label == "config"
