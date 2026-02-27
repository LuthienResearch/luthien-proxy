"""Base class for all policies.

This module provides the minimal base class that all policies inherit from,
providing common functionality like the short_policy_name property and
automatic get_config() for Pydantic-based configs.
"""

from __future__ import annotations

from collections.abc import MutableMapping, MutableSequence, MutableSet
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class BasePolicy:
    """Base class for all policies.

    Provides common functionality shared by all policy types:
    - short_policy_name property for human-readable identification
    - get_config() method for serializing policy configuration

    Policies should inherit from this class and one or more interface ABCs
    (OpenAIPolicyInterface, AnthropicPolicyInterface) to define which
    API formats they support.
    """

    _FROZEN_ATTR = "_policy_state_frozen"
    _ALLOWED_MUTABLE_INSTANCE_ATTRS: frozenset[str] = frozenset()

    def __setattr__(self, name: str, value: Any) -> None:
        """Disallow post-configuration attribute mutation on policy instances."""
        if name == self._FROZEN_ATTR:
            object.__setattr__(self, name, value)
            return

        if getattr(self, self._FROZEN_ATTR, False):
            raise AttributeError(
                f"{self.__class__.__name__} is frozen after configuration. "
                "Use PolicyContext state slots for request-scoped mutable state."
            )

        object.__setattr__(self, name, value)

    def freeze_configured_state(self) -> None:
        """Freeze instance attributes after configuration and validate statelessness."""
        self._validate_no_mutable_instance_state()
        object.__setattr__(self, self._FROZEN_ATTR, True)

    def _validate_no_mutable_instance_state(self) -> None:
        """Fail if policy instance contains mutable containers likely used as runtime state."""
        mutable_types: tuple[type[Any], ...] = (MutableMapping, MutableSequence, MutableSet, bytearray)

        for attr_name, value in vars(self).items():
            if attr_name == self._FROZEN_ATTR or attr_name in self._ALLOWED_MUTABLE_INSTANCE_ATTRS:
                continue
            if isinstance(value, mutable_types):
                raise TypeError(
                    f"{self.__class__.__name__}.{attr_name} is a mutable container ({type(value).__name__}). "
                    "Policy instances must be stateless after configuration; use PolicyContext state slots instead."
                )

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
        are Pydantic models. When there's a single Pydantic model attribute,
        returns its fields directly (flat) for clean API round-tripping.

        Returns:
            Dict of configuration values.
        """
        config: dict[str, Any] = {}

        for attr_name, value in vars(self).items():
            if attr_name.startswith("_"):
                continue

            if isinstance(value, BaseModel):
                config[attr_name] = value.model_dump()

        # Single Pydantic config model: return its fields directly
        if len(config) == 1:
            return next(iter(config.values()))

        return config

    @staticmethod
    def _init_config(config: T | dict[str, Any] | None, config_class: type[T]) -> T:
        """Parse a config value into a Pydantic model.

        Handles the three forms every policy __init__ receives:
        None (use defaults), dict (from policy manager), or an already-parsed model.
        """
        if config is None:
            return config_class()
        if isinstance(config, dict):
            return config_class.model_validate(config)
        return config


__all__ = ["BasePolicy"]
