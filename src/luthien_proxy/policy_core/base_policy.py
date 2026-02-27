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

    Policies should inherit from this class and one or more policy contracts
    (OpenAIPolicyInterface, AnthropicExecutionInterface) to define which
    API formats they support.
    """

    def freeze_configured_state(self) -> None:
        """Validate configured instance shape.

        This is intentionally a lightweight one-time guard run at policy load time.
        It validates that public configuration attributes are not mutable containers,
        but does not freeze runtime attribute assignment.
        """
        self._validate_no_mutable_instance_state()

    def _validate_no_mutable_instance_state(self) -> None:
        """Fail if public instance attrs contain mutable containers.

        Public mutable attrs are likely runtime state accidentally stored on a long-lived
        policy instance. Private attrs (leading underscore) are treated as internal
        implementation details and are not validated here.
        """
        mutable_types: tuple[type[Any], ...] = (MutableMapping, MutableSequence, MutableSet, bytearray)

        for attr_name, value in vars(self).items():
            if attr_name.startswith("_"):
                continue
            if isinstance(value, mutable_types):
                raise TypeError(
                    f"{self.__class__.__name__}.{attr_name} is a mutable container ({type(value).__name__}). "
                    "Public policy attrs should be immutable config values; keep request state in PolicyContext."
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
