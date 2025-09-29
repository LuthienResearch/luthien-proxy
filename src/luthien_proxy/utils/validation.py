"""Lightweight runtime validation helpers used across the control plane."""

from __future__ import annotations

from typing import Type, TypeVar, cast

T = TypeVar("T")


def require_type(value: object, expected_type: Type[T], label: str = "value") -> T:
    """Return *value* if it matches *expected_type*, else raise ``ValueError``.

    Args:
        value: The value to check and return.
        expected_type: The expected type of *value*.
        label: Optional label to use in error messages (e.g. "{label} must be a str").
    """
    if isinstance(value, expected_type):
        return cast(T, value)
    try:
        return T(value)  # type: ignore
    except (TypeError, ValueError) as e:
        raise ValueError(f"{label} could not be cast to {expected_type}: {e}") from e


__all__ = ["require_type"]
