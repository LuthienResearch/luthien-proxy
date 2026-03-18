"""Shared utilities for Multi* policy implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from luthien_proxy.policy_core import BasePolicy


def load_sub_policy(policy_config: dict[str, Any]) -> BasePolicy:
    """Load a single sub-policy from its config dict.

    Reuses the existing config loading machinery so nested policies
    (including other Multi* policies) work recursively.

    Args:
        policy_config: Dict with 'class' (import path) and optional 'config' (params)

    Returns:
        Instantiated policy object
    """
    from luthien_proxy.config import _import_policy_class, _instantiate_policy  # noqa: PLC0415

    class_ref = policy_config["class"]
    config = policy_config.get("config", {})
    policy_class = _import_policy_class(class_ref)
    return _instantiate_policy(policy_class, config)


def validate_sub_policies_interface(
    sub_policies: tuple[BasePolicy, ...],
    interface: type,
    interface_name: str,
    caller_name: str,
) -> None:
    """Raise TypeError if any sub-policy doesn't implement the required interface."""
    for policy in sub_policies:
        if not isinstance(policy, interface):
            raise TypeError(
                f"Policy '{policy.short_policy_name}' ({type(policy).__name__}) does not implement "
                f"{interface_name}, but {caller_name} received a {interface_name} call. "
                f"All sub-policies must implement the interface being called."
            )


__all__ = ["load_sub_policy", "validate_sub_policies_interface"]
