# ABOUTME: Unit tests for DefaultPolicyExecutor
# ABOUTME: Tests keepalive mechanism and timeout tracking

"""Tests for DefaultPolicyExecutor."""

import time

from luthien_proxy.v2.streaming.policy_executor import DefaultPolicyExecutor


class TestDefaultPolicyExecutor:
    """Tests for DefaultPolicyExecutor."""

    def test_initialization(self):
        """DefaultPolicyExecutor initializes with policy and timeout."""
        mock_policy = object()
        executor = DefaultPolicyExecutor(policy=mock_policy, timeout_seconds=30.0)

        assert executor.policy is mock_policy
        assert executor.timeout_seconds == 30.0

    def test_initialization_without_timeout(self):
        """DefaultPolicyExecutor can be initialized without timeout."""
        mock_policy = object()
        executor = DefaultPolicyExecutor(policy=mock_policy, timeout_seconds=None)

        assert executor.policy is mock_policy
        assert executor.timeout_seconds is None

    def test_keepalive_resets_timer(self):
        """Calling keepalive() resets the internal timer."""
        executor = DefaultPolicyExecutor(policy=object(), timeout_seconds=10.0)

        # Initial time_since_keepalive should be near zero
        initial_time = executor._time_since_keepalive()
        assert initial_time < 0.1  # Should be very small

        # Wait a bit
        time.sleep(0.15)
        before_keepalive = executor._time_since_keepalive()
        assert before_keepalive >= 0.15

        # Call keepalive
        executor.keepalive()

        # Time should reset to near zero
        after_keepalive = executor._time_since_keepalive()
        assert after_keepalive < 0.1

    def test_time_since_keepalive_increases(self):
        """time_since_keepalive() increases as time passes."""
        executor = DefaultPolicyExecutor(policy=object(), timeout_seconds=10.0)

        time1 = executor._time_since_keepalive()
        time.sleep(0.1)
        time2 = executor._time_since_keepalive()

        assert time2 > time1
        assert time2 - time1 >= 0.1

    def test_multiple_keepalives(self):
        """Multiple keepalive calls each reset the timer."""
        executor = DefaultPolicyExecutor(policy=object(), timeout_seconds=10.0)

        # First keepalive
        time.sleep(0.1)
        executor.keepalive()
        assert executor._time_since_keepalive() < 0.05

        # Second keepalive
        time.sleep(0.1)
        executor.keepalive()
        assert executor._time_since_keepalive() < 0.05

        # Third keepalive
        time.sleep(0.1)
        executor.keepalive()
        assert executor._time_since_keepalive() < 0.05
