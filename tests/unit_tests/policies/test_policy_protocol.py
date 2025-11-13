# ABOUTME: Tests for PolicyProtocol short_policy_name property

"""Tests for PolicyProtocol short_policy_name property."""

from luthien_proxy.policies.all_caps_policy import AllCapsPolicy
from luthien_proxy.policies.base_policy import BasePolicy
from luthien_proxy.policies.debug_logging_policy import DebugLoggingPolicy
from luthien_proxy.policies.noop_policy import NoOpPolicy
from luthien_proxy.policies.simple_policy import SimplePolicy
from luthien_proxy.policies.tool_call_judge_policy import ToolCallJudgePolicy


class TestPolicyProtocolShortName:
    """Tests for the short_policy_name property required by PolicyProtocol."""

    def test_base_policy_has_short_name(self):
        """Test that BasePolicy has short_policy_name property."""
        policy = BasePolicy()
        assert hasattr(policy, "short_policy_name")
        assert isinstance(policy.short_policy_name, str)
        assert policy.short_policy_name == "BasePolicy"

    def test_noop_policy_has_short_name(self):
        """Test that NoOpPolicy has short_policy_name property."""
        policy = NoOpPolicy()
        assert hasattr(policy, "short_policy_name")
        assert isinstance(policy.short_policy_name, str)
        # NoOpPolicy inherits from BasePolicy, so it should have class name
        assert policy.short_policy_name == "NoOpPolicy"

    def test_all_caps_policy_has_short_name(self):
        """Test that AllCapsPolicy has short_policy_name property."""
        policy = AllCapsPolicy()
        assert hasattr(policy, "short_policy_name")
        assert isinstance(policy.short_policy_name, str)
        # AllCapsPolicy inherits from BasePolicy, so it should have class name
        assert policy.short_policy_name == "AllCapsPolicy"

    def test_debug_logging_policy_has_short_name(self):
        """Test that DebugLoggingPolicy has short_policy_name property."""
        policy = DebugLoggingPolicy()
        assert hasattr(policy, "short_policy_name")
        assert isinstance(policy.short_policy_name, str)
        # DebugLoggingPolicy implements PolicyProtocol directly
        assert policy.short_policy_name == "DebugLogging"

    def test_tool_call_judge_policy_has_short_name(self):
        """Test that ToolCallJudgePolicy has short_policy_name property."""
        policy = ToolCallJudgePolicy()
        assert hasattr(policy, "short_policy_name")
        assert isinstance(policy.short_policy_name, str)
        # ToolCallJudgePolicy implements PolicyProtocol directly
        assert policy.short_policy_name == "ToolJudge"

    def test_simple_policy_has_short_name(self):
        """Test that SimplePolicy has short_policy_name property."""
        policy = SimplePolicy()
        assert hasattr(policy, "short_policy_name")
        assert isinstance(policy.short_policy_name, str)
        # SimplePolicy defaults to class name
        assert policy.short_policy_name == "SimplePolicy"

    def test_all_policies_have_short_name(self):
        """Test that all policy implementations have short_policy_name property."""
        policies = [
            BasePolicy(),
            NoOpPolicy(),
            AllCapsPolicy(),
            DebugLoggingPolicy(),
            ToolCallJudgePolicy(),
            SimplePolicy(),
        ]

        for policy in policies:
            assert hasattr(policy, "short_policy_name"), f"{policy.__class__.__name__} missing short_policy_name"
            assert isinstance(policy.short_policy_name, str), (
                f"{policy.__class__.__name__}.short_policy_name is not a string"
            )
            assert len(policy.short_policy_name) > 0, f"{policy.__class__.__name__}.short_policy_name is empty"
