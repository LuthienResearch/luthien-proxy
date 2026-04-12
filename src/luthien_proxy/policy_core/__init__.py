"""Shared policy contracts and utilities.

This module provides the neutral contract layer that decouples policies
from streaming. Both modules import from this policy_core layer to avoid
circular dependencies.

Policy Interfaces:
- BasePolicy: Base class providing common functionality
- AnthropicExecutionInterface: hook-based Anthropic policy contract
- AnthropicHookPolicy: mixin with passthrough defaults for all hooks
"""

from luthien_proxy.policy_core.anthropic_execution_interface import (
    AnthropicExecutionInterface,
    AnthropicPolicyEmission,
)
from luthien_proxy.policy_core.anthropic_hook_policy import AnthropicHookPolicy
from luthien_proxy.policy_core.base_policy import BasePolicy, PolicyLoadContext
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.policy_core.text_modifier_policy import TextModifierPolicy

__all__ = [
    # ABC-based interfaces (preferred for new code)
    "BasePolicy",
    "PolicyLoadContext",
    "AnthropicExecutionInterface",
    "AnthropicPolicyEmission",
    "AnthropicHookPolicy",
    # Contexts
    "PolicyContext",
    # Text modifier base class
    "TextModifierPolicy",
]
