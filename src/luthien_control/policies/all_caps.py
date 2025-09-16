"""
Toy ALL-CAPS policy for testing request/response modification.

Behavior:
- Pre hook: pass-through
- Post-success: replace final response with ALL-CAPS content
- Streaming: uppercase per-chunk delta content via `edit` action
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .base import LuthienPolicy


class AllCapsPolicy(LuthienPolicy):
    async def async_post_call_streaming_iterator_hook(
        self,
        data: Dict[str, Any],
        user_api_key_dict: Optional[Dict[str, Any]],
        response: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        # Create a shallow copy and uppercase assistant message content
        try:
            response = dict(response)
            choices = response.get("response_obj").get("choices")
            for c in choices:
                c["delta"]["content"] = c["delta"]["content"].upper()
            return response
        except Exception:
            # On any failure, keep original
            return response
