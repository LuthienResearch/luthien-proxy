"""Unit tests for PolicyManager dogfood composition integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from luthien_proxy.policies.dogfood_safety_policy import DogfoodSafetyPolicy
from luthien_proxy.policies.multi_serial_policy import MultiSerialPolicy
from luthien_proxy.policies.noop_policy import NoOpPolicy
from luthien_proxy.policy_manager import PolicyManager
from luthien_proxy.settings import Settings


def _make_manager() -> PolicyManager:
    """Create a PolicyManager with mock DB and Redis."""
    return PolicyManager(db_pool=MagicMock(), redis_client=MagicMock())


def _settings_with_dogfood(enabled: bool) -> Settings:
    """Create a Settings instance with dogfood_mode set."""
    return Settings(
        dogfood_mode=enabled,
        database_url="",
        redis_url="",
        _env_file=None,  # type: ignore[call-arg]
    )


class TestMaybeComposeDogfoodDisabled:
    """When dogfood_mode=False, policies pass through unchanged."""

    def test_noop_policy_returned_unchanged(self):
        manager = _make_manager()
        policy = NoOpPolicy()

        with patch("luthien_proxy.settings.get_settings", return_value=_settings_with_dogfood(False)):
            result = manager._maybe_compose_dogfood(policy)

        assert result is policy

    def test_multi_serial_policy_returned_unchanged(self):
        manager = _make_manager()
        policy = MultiSerialPolicy.from_instances([NoOpPolicy()])

        with patch("luthien_proxy.settings.get_settings", return_value=_settings_with_dogfood(False)):
            result = manager._maybe_compose_dogfood(policy)

        assert result is policy


class TestMaybeComposeDogfoodEnabled:
    """When dogfood_mode=True, policies get composed with DogfoodSafetyPolicy."""

    def test_wraps_plain_policy(self):
        manager = _make_manager()
        policy = NoOpPolicy()

        with patch("luthien_proxy.settings.get_settings", return_value=_settings_with_dogfood(True)):
            result = manager._maybe_compose_dogfood(policy)

        assert isinstance(result, MultiSerialPolicy)
        assert len(result._sub_policies) == 2
        assert isinstance(result._sub_policies[0], DogfoodSafetyPolicy)
        assert isinstance(result._sub_policies[1], NoOpPolicy)

    def test_dogfood_safety_runs_first(self):
        """DogfoodSafetyPolicy must be first so safety checks run before user policy."""
        manager = _make_manager()
        policy = NoOpPolicy()

        with patch("luthien_proxy.settings.get_settings", return_value=_settings_with_dogfood(True)):
            result = manager._maybe_compose_dogfood(policy)

        assert isinstance(result, MultiSerialPolicy)
        assert isinstance(result._sub_policies[0], DogfoodSafetyPolicy)

    def test_wraps_multi_serial_without_dogfood(self):
        manager = _make_manager()
        multi = MultiSerialPolicy.from_instances([NoOpPolicy(), NoOpPolicy()])

        with patch("luthien_proxy.settings.get_settings", return_value=_settings_with_dogfood(True)):
            result = manager._maybe_compose_dogfood(multi)

        assert isinstance(result, MultiSerialPolicy)
        assert result is not multi
        assert len(result._sub_policies) == 3
        assert isinstance(result._sub_policies[0], DogfoodSafetyPolicy)


class TestMaybeComposeDogfoodNoDoubleWrap:
    """When DogfoodSafetyPolicy is already in the chain, don't double-compose."""

    def test_already_wrapped_returns_same(self):
        manager = _make_manager()
        already_wrapped = MultiSerialPolicy.from_instances([DogfoodSafetyPolicy(), NoOpPolicy()])

        with patch("luthien_proxy.settings.get_settings", return_value=_settings_with_dogfood(True)):
            result = manager._maybe_compose_dogfood(already_wrapped)

        assert result is already_wrapped

    def test_dogfood_anywhere_in_chain_prevents_wrap(self):
        """DogfoodSafetyPolicy anywhere in the chain prevents double-wrapping."""
        manager = _make_manager()
        policy = MultiSerialPolicy.from_instances([NoOpPolicy(), DogfoodSafetyPolicy()])

        with patch("luthien_proxy.settings.get_settings", return_value=_settings_with_dogfood(True)):
            result = manager._maybe_compose_dogfood(policy)

        assert result is policy

    def test_standalone_dogfood_returns_same(self):
        """If the policy IS a DogfoodSafetyPolicy, return it unchanged."""
        manager = _make_manager()
        policy = DogfoodSafetyPolicy()

        with patch("luthien_proxy.settings.get_settings", return_value=_settings_with_dogfood(True)):
            result = manager._maybe_compose_dogfood(policy)

        assert result is policy
