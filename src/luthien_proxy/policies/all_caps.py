"""Toy ALL-CAPS policy for testing request/response modification.

Behavior:
- Pre hook: pass-through
- Post-success: replace final response with ALL-CAPS content
- Streaming: uppercase per-chunk delta content via `edit` action
"""

from __future__ import annotations

from copy import deepcopy
from typing import AsyncIterator

from luthien_proxy.control_plane.conversation.utils import require_dict, require_list
from luthien_proxy.types import JSONObject

from .base import LuthienPolicy, StreamPolicyContext


def _uppercase_choices(response: JSONObject) -> JSONObject:
    mutated = deepcopy(response)
    choices = require_list(mutated.get("choices"), "response choices")
    for index, choice_value in enumerate(choices):
        choice = require_dict(choice_value, f"response choice #{index}")
        if "delta" in choice:
            delta = require_dict(choice["delta"], f"response choice #{index}.delta")
            content = delta.get("content")
            if isinstance(content, str):
                delta["content"] = content.upper()
        if "message" in choice:
            message = require_dict(choice["message"], f"response choice #{index}.message")
            content = message.get("content")
            if isinstance(content, str):
                message["content"] = content.upper()
    return mutated


class AllCapsPolicy(LuthienPolicy):
    """Demonstration policy that uppercases content in responses."""

    async def generate_response_stream(
        self,
        context: StreamPolicyContext,
        incoming_stream: AsyncIterator[JSONObject],
    ) -> AsyncIterator[JSONObject]:
        """Yield incoming chunks with text content uppercased."""
        async for chunk in incoming_stream:
            context.chunk_count += 1
            yield _uppercase_choices(chunk)

    async def async_post_call_success_hook(
        self,
        *,
        response_obj: JSONObject,
        **_unused: object,
    ) -> JSONObject:
        """Uppercase content in a non-streaming final response."""
        return _uppercase_choices(response_obj)
