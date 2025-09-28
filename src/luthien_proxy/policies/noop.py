"""Default no-op policy that mirrors LiteLLM hook signatures and does nothing.

Users can implement their own policies by providing the same methods and
setting the LUTHIEN_POLICY env var to "module.path:ClassName".
"""

from typing import Optional

from luthien_proxy.types import JSONObject

from .base import LuthienPolicy


class NoOpPolicy(LuthienPolicy):
    """Policy that intentionally performs no modifications."""

    async def async_post_call_success_hook(
        self,
        data: JSONObject,
        user_api_key_dict: Optional[JSONObject],
        response: JSONObject,
    ) -> Optional[JSONObject]:
        """Return the unmodified final response for non-streaming calls."""
        return response
