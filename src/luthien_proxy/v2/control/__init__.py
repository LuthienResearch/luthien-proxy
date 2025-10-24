# ABOUTME: Control plane module - policy execution orchestration
# ABOUTME: Provides high-level control plane for executing policies on requests

"""Control plane interface and implementations."""

from .synchronous_control_plane import SynchronousControlPlane

__all__ = [
    "SynchronousControlPlane",
]
