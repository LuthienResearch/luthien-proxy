"""
Default no-op policy that mirrors LiteLLM hook signatures and does nothing.

Users can implement their own policies by providing the same methods and
setting the LUTHIEN_POLICY env var to "module.path:ClassName".
"""

from typing import Any, Dict, Optional

from .base import LuthienPolicy


class NoOpPolicy(LuthienPolicy):
    async def async_post_call_success_hook(
        self,
        data: Dict[str, Any],
        user_api_key_dict: Optional[Dict[str, Any]],
        response: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        # Create a shallow copy and uppercase assistant message content
        return response
