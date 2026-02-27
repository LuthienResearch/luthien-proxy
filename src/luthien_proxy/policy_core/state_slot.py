"""Typed request-scoped state slots for policies.

StateSlot provides a framework primitive for storing strongly-typed per-request
state on PolicyContext without leaking mutable state onto policy instances.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class StateSlot(Generic[T]):
    """Typed key for request-scoped policy state."""

    name: str
    expected_type: type[T]
    factory: Callable[[], T]
