"""Base class for all policies.

This module provides the minimal base class that all policies inherit from,
providing common functionality like the short_policy_name property and
automatic get_config() for Pydantic-based configs.
"""

from __future__ import annotations

from collections.abc import MutableMapping, MutableSequence, MutableSet
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


# ============================================================
# UI catalog rendering metadata
#
# Everything in this section is consumed by the policy-config catalog UI
# (src/luthien_proxy/static/policy_config.js) via the discovery API.
# It has NO impact on policy execution, scheduling, or runtime behavior.
# ============================================================


class Category(StrEnum):
    """Top-level Available-column section in the policy-config catalog UI.

    Pure UI rendering concept; no runtime effect.
    """

    SIMPLE_UTILITIES = "simple_utilities"
    ACTIVE_MONITORING = "active_monitoring"
    FUN_AND_GOOFY = "fun_and_goofy"
    ADVANCED = "advanced"
    INTERNAL = "internal"


class CatalogBadge(StrEnum):
    """Tag chips rendered next to the policy name in the catalog.

    Pure UI rendering concept; no runtime effect.
    """

    BLOCKS = "Blocks"
    JUDGE = "Judge"


@dataclass(frozen=True)
class UIMetadata:
    """UI-only catalog rendering metadata. NO runtime effect.

    Every field here is consumed only by the policy-config catalog UI
    (``src/luthien_proxy/static/policy_config.js``) via the discovery API.
    Nothing here influences policy execution, scheduling, or output.

    To add a new UI catalog field, add it here — not on ``BasePolicy``.
    """

    display_name: str = ""
    """Friendly name shown in the catalog (e.g. "De-Slop").

    Falls back to a regex-derived name from the class if empty."""

    short_description: str = ""
    """One-line description shown under the display name in the catalog."""

    category: Category = Category.ADVANCED
    """Top-level Available-column section in the catalog."""

    catalog_badges: tuple[CatalogBadge, ...] = ()
    """Tag chips rendered next to the policy name (e.g. "Blocks", "Judge")."""

    ui_policy_preview: str = ""
    """Preview shown in the catalog under "What this policy does" / similar.

    PREVIEW ONLY — NOT a runtime contract. Production output may differ:
    - For LLM-judge blocking policies (SimpleLLMPolicy subclasses), the
      runtime block message is judge-LLM output, not this string.
    - For policies with templated runtime alerts (ToolCallJudgePolicy,
      DogfoodSafetyPolicy), the runtime template includes dynamic data
      (tool names, probabilities, command text) not present here.

    Use this field for at-a-glance UI hints only. If a policy's runtime
    output diverges meaningfully from this preview, add an inline comment
    on the ``ui = UIMetadata(...)`` block flagging where the production
    output differs.
    """


# ============================================================
# BasePolicy
# ============================================================


class BasePolicy:
    """Base class for all policies.

    **Statelessness invariant:** Policy instances are singletons created once at
    startup and shared across all concurrent requests. They must never hold
    request-scoped mutable state. Per-request data belongs on ``PolicyContext``
    (via ``get_request_state()``) or on the request-scoped IO object.

    ``freeze_configured_state()`` enforces this at load time by rejecting mutable
    container attributes on the policy instance.

    Provides common functionality shared by all policy types:
    - short_policy_name property for human-readable identification
    - get_config() method for serializing policy configuration
    - ``ui`` class attribute (``UIMetadata``) for catalog rendering only

    Policies should inherit from this class and implement AnthropicExecutionInterface
    to define the policy execution behavior.
    """

    ui: UIMetadata = UIMetadata()
    """UI-only catalog rendering metadata. Override per-policy as needed.

    Subclasses replace the entire UIMetadata; partial overrides aren't supported
    (use ``dataclasses.replace(BasePolicy.ui, display_name=...)`` if you want
    to compose, but in practice each policy declares its own values).
    """

    def freeze_configured_state(self) -> None:
        """Validate configured instance shape.

        This is intentionally a lightweight one-time guard run at policy load time.
        It validates that public configuration attributes are not mutable containers,
        but does not freeze runtime attribute assignment.
        """
        self._validate_no_mutable_instance_state()

    def _validate_no_mutable_instance_state(self) -> None:
        """Fail if any instance attrs contain mutable containers.

        Policies are long-lived singletons shared across concurrent requests.
        Mutable containers on the instance are almost certainly bugs — use
        tuple/frozenset for config-time collections and ``PolicyContext`` for
        request-scoped state.
        """
        mutable_types: tuple[type, ...] = (MutableMapping, MutableSequence, MutableSet, bytearray)

        for attr_name, value in vars(self).items():
            if isinstance(value, mutable_types):
                raise TypeError(
                    f"{self.__class__.__name__}.{attr_name} is a mutable container ({type(value).__name__}). "
                    "Policy attrs must be immutable (use tuple/frozenset); "
                    "keep request state in PolicyContext."
                )

    @property
    def short_policy_name(self) -> str:
        """Short human-readable name for the policy.

        Returns the class name by default. Subclasses can override
        for a custom name (e.g., 'NoOp', 'AllCaps', 'ToolJudge').
        """
        return self.__class__.__name__

    def active_policy_names(self) -> list[str]:
        """Return this policy's name as an active leaf policy.

        Multi-policies override this to recurse into sub-policies.
        NoOpPolicy overrides to return [].
        """
        return [self.short_policy_name]

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


__all__ = [
    "BasePolicy",
    "Category",
    "CatalogBadge",
    "UIMetadata",
]
