"""Inject policy awareness into LLM requests.

When active policies modify the LLM's output (e.g. uppercasing, replacing text),
the model can get confused because it doesn't know its responses are being
transformed. This module injects a brief system-level note informing the model
about active policies so it doesn't fight the transformations.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from luthien_proxy.llm.types import Request, SystemMessage
    from luthien_proxy.llm.types.anthropic import (
        AnthropicRequest,
        AnthropicSystemBlock,
    )

logger = logging.getLogger(__name__)

POLICY_AWARENESS_PREFIX = "[Luthien Proxy]"

_AWARENESS_TEMPLATE = (
    f"{POLICY_AWARENESS_PREFIX} Your responses may be modified by the following "
    "active policies before reaching the user: {policy_names}. "
    "This is expected behavior — do not try to compensate for or reverse "
    "these modifications."
)


def build_awareness_message(policy_names: list[str]) -> str:
    """Build the awareness message text from a list of policy names."""
    return _AWARENESS_TEMPLATE.format(policy_names=", ".join(policy_names))


def inject_policy_awareness_openai(request: Request, policy_names: list[str]) -> Request:
    """Inject a policy awareness system message into an OpenAI-format request.

    Appends a brief note to the existing system message (or adds a new one)
    informing the model about active policies.

    Returns the request unchanged if policy_names is empty.
    """
    if not policy_names:
        return request

    awareness_text = build_awareness_message(policy_names)
    logger.debug(f"Injecting policy awareness for policies: {policy_names}")

    messages = list(request.messages)

    existing_system_idx = _find_system_message_index(messages)
    if existing_system_idx is not None:
        existing = messages[existing_system_idx]
        current_content = existing.get("content", "")
        if isinstance(current_content, list):
            new_content: str | list[Any] = current_content + [{"type": "text", "text": awareness_text}]
        else:
            text = current_content if isinstance(current_content, str) else ""
            new_content = text + "\n\n" + awareness_text
        updated: SystemMessage = {**existing, "content": new_content}  # type: ignore[typeddict-item]
        messages[existing_system_idx] = updated
    else:
        new_system: SystemMessage = {"role": "system", "content": awareness_text}
        messages.insert(0, new_system)

    return request.model_copy(update={"messages": messages})


def inject_policy_awareness_anthropic(request: AnthropicRequest, policy_names: list[str]) -> AnthropicRequest:
    """Inject a policy awareness system message into an Anthropic-format request.

    Appends a text block to the existing system content (or adds a new one)
    informing the model about active policies.

    Returns the request unchanged if policy_names is empty.
    """
    if not policy_names:
        return request

    awareness_text = build_awareness_message(policy_names)
    logger.debug(f"Injecting policy awareness for policies: {policy_names}")

    existing_system = request.get("system")

    if existing_system is None:
        request = {**request, "system": awareness_text}
    elif isinstance(existing_system, str):
        request = {**request, "system": existing_system + "\n\n" + awareness_text}
    else:
        awareness_block: AnthropicSystemBlock = {"type": "text", "text": awareness_text}
        request = {**request, "system": list(existing_system) + [awareness_block]}

    return request


def _find_system_message_index(messages: Sequence[Any]) -> int | None:
    """Find the index of the first system message, if any."""
    for i, msg in enumerate(messages):
        if isinstance(msg, dict) and msg.get("role") == "system":
            return i
    return None


__all__ = [
    "inject_policy_awareness_openai",
    "inject_policy_awareness_anthropic",
    "build_awareness_message",
]
