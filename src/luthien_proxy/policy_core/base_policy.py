# ABOUTME: Base class for all policies providing common functionality

"""Base class for all policies.

This module provides the minimal base class that all policies inherit from,
providing common functionality like the short_policy_name property and
automatic get_config() for Pydantic-based configs.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class BasePolicy:
    """Base class for all policies.

    Provides common functionality shared by all policy types:
    - short_policy_name property for human-readable identification
    - get_config() method for serializing policy configuration

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

    def get_config(self) -> dict[str, Any]:
        """Get the configuration for this policy instance.

        Automatically extracts configuration from instance attributes that
        are Pydantic models. For each attribute that is a BaseModel, it's
        serialized using model_dump().

        Returns:
            Dict mapping attribute names to their values (Pydantic models
            are converted to dicts).
        """
        config: dict[str, Any] = {}

        for attr_name, value in vars(self).items():
            if attr_name.startswith("_"):
                continue

            if isinstance(value, BaseModel):
                config[attr_name] = value.model_dump()

        return config


__all__ = ["BasePolicy"]
