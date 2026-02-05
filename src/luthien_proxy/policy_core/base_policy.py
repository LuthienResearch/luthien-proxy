# ABOUTME: Base class for all policies providing common functionality

"""Base class for all policies.

This module provides the minimal base class that all policies inherit from,
providing common functionality like the short_policy_name property.
"""

from __future__ import annotations


class BasePolicy:
    """Base class for all policies.

    Provides common functionality shared by all policy types:
    - short_policy_name property for human-readable identification

    Policies should inherit from this class and one or more interface ABCs
    (OpenAIPolicyInterface, AnthropicPolicyInterface) to define which
    API formats they support.
    """

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name for the policy.

        Returns the class name by default. Subclasses can override
        for a custom name (e.g., 'NoOp', 'AllCaps', 'ToolJudge').
        """
        return self.__class__.__name__


__all__ = ["BasePolicy"]
