"""Utilities for working with OpenAI-style streaming chunks."""

from __future__ import annotations

from luthien_proxy.control_plane.conversation.utils import require_dict, require_list
from luthien_proxy.types import JSONObject


def extract_delta_text(chunk: JSONObject) -> str:
    """Extract text delta from an OpenAI-style streaming chunk (best-effort)."""
    choices_value = chunk.get("choices")
    if choices_value is None:
        return ""
    choices = require_list(choices_value, "stream chunk choices")
    if not choices:
        return ""
    parts: list[str] = []
    for index, choice_value in enumerate(choices):
        if not isinstance(choice_value, dict):
            continue
        choice = require_dict(choice_value, f"stream chunk choice #{index}")
        delta_value = choice.get("delta")
        if not isinstance(delta_value, dict):
            continue
        delta = require_dict(delta_value, f"stream chunk choice #{index}.delta")
        content = delta.get("content")
        if isinstance(content, str):
            parts.append(content)
    return "".join(parts)
