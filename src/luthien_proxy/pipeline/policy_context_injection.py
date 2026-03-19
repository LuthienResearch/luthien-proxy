"""Inject policy awareness into LLM requests.

When active policies modify the LLM's output (e.g. uppercasing, replacing text),
the model can get confused because it doesn't know its responses are being
transformed. This module injects a one-time note into the first user message
informing the model about active policies. Because clients echo back conversation
history, the injected context persists across the session without re-injection.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from luthien_proxy.llm.types.anthropic import (
        AnthropicRequest,
    )

logger = logging.getLogger(__name__)

_CONTEXT_TAG = "policy-context"
_CONTEXT_OPEN = f"<{_CONTEXT_TAG}>"
_CONTEXT_CLOSE = f"</{_CONTEXT_TAG}>"

_AWARENESS_TEMPLATE = (
    f"{_CONTEXT_OPEN}Your responses may be modified by the following active "
    "policies before reaching the user: {policy_names}. This is expected "
    f"behavior — do not try to compensate for or reverse these modifications.{_CONTEXT_CLOSE}"
)


def build_awareness_message(policy_names: list[str]) -> str:
    """Build the awareness message text from a list of policy names."""
    return _AWARENESS_TEMPLATE.format(policy_names=", ".join(policy_names))


def _already_injected(messages: list[Any]) -> bool:
    """Check if any message already contains the policy context tag."""
    for msg in messages:
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        if isinstance(content, str) and _CONTEXT_OPEN in content:
            return True
        if isinstance(content, list):
            for block in content:
                text = block.get("text", "") if isinstance(block, dict) else ""
                if _CONTEXT_OPEN in text:
                    return True
    return False


def _find_first_user_message_index(messages: list[Any]) -> int | None:
    """Find the index of the first user message."""
    for i, msg in enumerate(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            return i
    return None



def inject_policy_awareness_anthropic(request: AnthropicRequest, policy_names: list[str]) -> AnthropicRequest:
    """Inject policy awareness into the first user message of an Anthropic-format request.

    Skips injection if policy_names is empty or the context tag is already present
    in any message (meaning it was injected on a previous turn).
    """
    if not policy_names:
        return request

    messages = list(request["messages"])
    if _already_injected(messages):
        return request

    user_idx = _find_first_user_message_index(messages)
    if user_idx is None:
        return request

    awareness_text = build_awareness_message(policy_names)
    logger.debug(f"Injecting policy awareness for policies: {policy_names}")

    user_msg = messages[user_idx]
    content = user_msg.get("content", "")
    if isinstance(content, list):
        injected_content: str | list[Any] = [{"type": "text", "text": awareness_text}] + content
    else:
        text = content if isinstance(content, str) else ""
        injected_content = awareness_text + "\n\n" + text
    messages[user_idx] = {**user_msg, "content": injected_content}  # type: ignore[typeddict-item]

    return {**request, "messages": messages}


__all__ = [
    "inject_policy_awareness_anthropic",
    "build_awareness_message",
]
