"""Unit tests for PolicyManager._maybe_wrap_for_dogfood().

Tests cover:
1. dogfood_mode=False returns policy unchanged
2. dogfood_mode=True wraps with MultiSerialPolicy containing [DogfoodSafetyPolicy, original]
3. dogfood_mode=True with already-wrapped policy does not double-wrap
4. dogfood_mode=True with MultiSerialPolicy that has no DogfoodSafetyPolicy still wraps
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from luthien_proxy.policies.dogfood_safety_policy import DogfoodSafetyPolicy
from luthien_proxy.policies.multi_serial_policy import MultiSerialPolicy
from luthien_proxy.policies.noop_policy import NoOpPolicy
from luthien_proxy.policy_manager import PolicyManager
from luthien_proxy.settings import Settings


def _make_manager() -> PolicyManager:
    """Create a PolicyManager with mock DB and Redis (no real connections)."""
    return PolicyManager(db_pool=MagicMock(), redis_client=MagicMock())


def _settings_with_dogfood(enabled: bool) -> Settings:
    """Create a Settings instance with dogfood_mode set."""
    return Settings(
        dogfood_mode=enabled,
        database_url="",
        redis_url="",
        _env_file=None,  # type: ignore[call-arg]
    )


class TestMaybeWrapForDogfoodDisabled:
    """When dogfood_mode=False, policies pass through unchanged."""

    def test_noop_policy_returned_unchanged(self):
        manager = _make_manager()
        policy = NoOpPolicy()

        with patch("luthien_proxy.settings.get_settings", return_value=_settings_with_dogfood(False)):
            result = manager._maybe_wrap_for_dogfood(policy)

        assert result is policy

    def test_multi_serial_policy_returned_unchanged(self):
        manager = _make_manager()
        policy = MultiSerialPolicy(
            policies=[
                {"class": "luthien_proxy.policies.noop_policy:NoOpPolicy", "config": {}},
            ]
        )

        with patch("luthien_proxy.settings.get_settings", return_value=_settings_with_dogfood(False)):
            result = manager._maybe_wrap_for_dogfood(policy)

        assert result is policy


class TestMaybeWrapForDogfoodEnabled:
    """When dogfood_mode=True, policies get wrapped with DogfoodSafetyPolicy."""

    def test_wraps_plain_policy_with_dogfood_safety(self):
        manager = _make_manager()
        policy = NoOpPolicy()

        with patch("luthien_proxy.settings.get_settings", return_value=_settings_with_dogfood(True)):
            result = manager._maybe_wrap_for_dogfood(policy)

        assert isinstance(result, MultiSerialPolicy)
        assert len(result._sub_policies) == 2
        assert isinstance(result._sub_policies[0], DogfoodSafetyPolicy)
        assert isinstance(result._sub_policies[1], NoOpPolicy)

    def test_dogfood_safety_runs_first_in_pipeline(self):
        """DogfoodSafetyPolicy must be first so safety checks run before user policy."""
        manager = _make_manager()
        policy = NoOpPolicy()

        with patch("luthien_proxy.settings.get_settings", return_value=_settings_with_dogfood(True)):
            result = manager._maybe_wrap_for_dogfood(policy)

        assert isinstance(result, MultiSerialPolicy)
        assert isinstance(result._sub_policies[0], DogfoodSafetyPolicy)


class TestMaybeWrapForDogfoodNoDoubleWrap:
    """When policy is already wrapped with DogfoodSafetyPolicy, don't double-wrap."""

    def test_already_wrapped_multi_serial_not_double_wrapped(self):
        manager = _make_manager()
        # Simulate an already-wrapped policy: MultiSerialPolicy([DogfoodSafety, NoOp])
        already_wrapped = MultiSerialPolicy(
            policies=[
                {"class": "luthien_proxy.policies.dogfood_safety_policy:DogfoodSafetyPolicy", "config": {}},
                {"class": "luthien_proxy.policies.noop_policy:NoOpPolicy", "config": {}},
            ]
        )

        with patch("luthien_proxy.settings.get_settings", return_value=_settings_with_dogfood(True)):
            result = manager._maybe_wrap_for_dogfood(already_wrapped)

        # Should return the same object, not wrap again
        assert result is already_wrapped
        assert len(already_wrapped._sub_policies) == 2

    def test_dogfood_safety_anywhere_in_multi_serial_prevents_wrap(self):
        """Even if DogfoodSafetyPolicy isn't first, its presence prevents double-wrap."""
        manager = _make_manager()
        policy_with_dogfood_second = MultiSerialPolicy(
            policies=[
                {"class": "luthien_proxy.policies.noop_policy:NoOpPolicy", "config": {}},
                {"class": "luthien_proxy.policies.dogfood_safety_policy:DogfoodSafetyPolicy", "config": {}},
            ]
        )

        with patch("luthien_proxy.settings.get_settings", return_value=_settings_with_dogfood(True)):
            result = manager._maybe_wrap_for_dogfood(policy_with_dogfood_second)

        assert result is policy_with_dogfood_second


class TestMaybeWrapForDogfoodMultiSerialWithoutDogfood:
    """MultiSerialPolicy without DogfoodSafetyPolicy still gets wrapped."""

    def test_multi_serial_without_dogfood_gets_wrapped(self):
        manager = _make_manager()
        # A MultiSerialPolicy with only NoOpPolicy sub-policies (no DogfoodSafetyPolicy)
        multi_without_dogfood = MultiSerialPolicy(
            policies=[
                {"class": "luthien_proxy.policies.noop_policy:NoOpPolicy", "config": {}},
                {"class": "luthien_proxy.policies.noop_policy:NoOpPolicy", "config": {}},
            ]
        )

        with patch("luthien_proxy.settings.get_settings", return_value=_settings_with_dogfood(True)):
            result = manager._maybe_wrap_for_dogfood(multi_without_dogfood)

        # Should be a NEW MultiSerialPolicy wrapping the original
        assert isinstance(result, MultiSerialPolicy)
        assert result is not multi_without_dogfood
        assert len(result._sub_policies) == 2
        assert isinstance(result._sub_policies[0], DogfoodSafetyPolicy)
        # The second sub-policy is a fresh MultiSerialPolicy (re-instantiated from config)
        assert isinstance(result._sub_policies[1], MultiSerialPolicy)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
