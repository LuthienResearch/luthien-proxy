"""Base class for all policies.

This module provides the minimal base class that all policies inherit from,
providing common functionality like the short_policy_name property and
automatic get_config() for Pydantic-based configs.
"""

from __future__ import annotations

from collections.abc import MutableMapping, MutableSequence, MutableSet
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel

if TYPE_CHECKING:
    from luthien_proxy.policy_core.policy_context import PolicyContext
    from luthien_proxy.types import RawHttpRequest
    from luthien_proxy.utils.db import DatabasePool

T = TypeVar("T", bound=BaseModel)


@dataclass
class PolicyLoadContext:
    """Gateway services available to policies after construction.

    Passed to ``on_policy_loaded()`` by ``PolicyManager.initialize()``.
    Policies that need shared infrastructure (db_pool, scheduler) read
    from here. Fields are optional — policies must handle ``None``.
    """

    db_pool: "DatabasePool | None" = None
    scheduler: Any = None


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

    Policies should inherit from this class and implement AnthropicExecutionInterface
    to define the policy execution behavior.
    """

    async def on_policy_loaded(self, context: PolicyLoadContext) -> None:
        """Called by PolicyManager after construction with gateway services.

        Policies that need shared infrastructure (db_pool, scheduler) override
        this method. Default implementation is a no-op.
        """
        pass

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

    @staticmethod
    def _extract_passthrough_key(raw_http_request: "RawHttpRequest | None") -> str | None:
        """Extract the upstream API key from the incoming request headers.

        Checks Authorization (Bearer) then x-api-key. Returns None if absent.
        Used to forward the client's own key to judge LLM calls.
        """
        if raw_http_request is None:
            return None
        headers = raw_http_request.headers
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:] or None
        return headers.get("x-api-key") or None

    def _resolve_judge_api_key(
        self,
        context: "PolicyContext",
        explicit_key: str | None,
        fallback_key: str | None,
    ) -> str | None:
        """Resolve the API key for judge LLM calls.

        Priority: explicit per-policy key → passthrough (client's key) → server fallback.
        """
        if explicit_key:
            return explicit_key
        passthrough = self._extract_passthrough_key(context.raw_http_request)
        return passthrough or fallback_key


__all__ = ["BasePolicy", "PolicyLoadContext"]
