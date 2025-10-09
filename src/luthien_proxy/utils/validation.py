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
        coerced = expected_type(value)  # type: ignore[call-arg]
    except Exception as exc:
        raise ValueError(f"{label} could not be cast to {expected_type}: {exc}") from exc
    if not isinstance(coerced, expected_type):
        raise ValueError(f"{label} could not be coerced to {expected_type}")
    return cast(T, coerced)


__all__ = ["require_type"]
