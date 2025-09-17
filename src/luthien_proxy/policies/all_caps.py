"""Toy ALL-CAPS policy for testing request/response modification.

Behavior:
- Pre hook: pass-through
- Post-success: replace final response with ALL-CAPS content
- Streaming: uppercase per-chunk delta content via `edit` action
"""

from __future__ import annotations

from typing import Any, Optional

from .base import LuthienPolicy


class AllCapsPolicy(LuthienPolicy):
    """Demonstration policy that uppercases content in responses."""

    async def async_post_call_streaming_iterator_hook(
        self,
        user_api_key_dict: Optional[dict[str, Any]],
        response: Any,
        request_data: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Uppercase streaming delta content per chunk when possible."""
        try:
            response = dict(response)
            for c in response.get("choices", []):
                c["delta"]["content"] = c["delta"]["content"].upper()
            return response
        except Exception:
            # On any failure, keep original
            return response

    async def async_post_call_success_hook(self, **kwargs: Any) -> dict[str, Any]:
        """Uppercase content in a non-streaming final response."""
        try:
            response = dict(kwargs.get("response_obj", {}))
            for c in response.get("choices", []):
                c["delta"]["content"] = c["delta"]["content"].upper()
            return response
        except Exception:
            # On any failure, keep original
            return kwargs.get("response_obj", {})
