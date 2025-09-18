"""Utilities for working with OpenAI-style streaming chunks."""

from __future__ import annotations

from typing import Any


def extract_delta_text(chunk: dict[str, Any]) -> str:
    """Extract text delta from an OpenAI-style streaming chunk (best-effort)."""
    try:
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        parts: list[str] = []
        for c in choices:
            if not isinstance(c, dict):
                continue
            delta = c.get("delta") or {}
            if not isinstance(delta, dict):
                continue
            t = delta.get("content")
            if isinstance(t, str):
                parts.append(t)
        return "".join(parts)
    except Exception:
        return ""
