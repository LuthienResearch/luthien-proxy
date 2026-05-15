"""Block descriptor used by judges and the message builder.

Light, neutral dataclass kept in `policy_core` so the builder can reference
it without importing the `policies` package (which would create a circular
import via re-export of policy classes through `policies/__init__.py`).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BlockDescriptor:
    """Describes a content block from the LLM response."""

    type: str
    content: str


__all__ = ["BlockDescriptor"]
