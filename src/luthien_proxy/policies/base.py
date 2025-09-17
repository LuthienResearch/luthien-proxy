"""
Abstract base for Luthien Control Policies.

Policies mirror LiteLLM's CustomLogger hook signatures to keep mental models
simple. The Control Plane endpoints invoke these methods and return JSON
encodings of their results.
"""

from __future__ import annotations

from abc import ABC

from litellm.integrations.custom_logger import CustomLogger


class LuthienPolicy(ABC, CustomLogger):
    """Mirror of LiteLLM hook API, executed server-side in the control plane."""

    pass
