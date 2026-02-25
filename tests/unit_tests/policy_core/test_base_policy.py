# ABOUTME: Unit tests for BasePolicy class

"""Unit tests for BasePolicy class."""

from luthien_proxy.policy_core.base_policy import BasePolicy


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
