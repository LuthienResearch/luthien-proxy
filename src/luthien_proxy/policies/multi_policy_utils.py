"""Shared utilities for Multi* policy implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from luthien_proxy.policy_core import PolicyProtocol


def load_sub_policy(policy_config: dict[str, Any]) -> PolicyProtocol:
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


__all__ = ["load_sub_policy"]
