"""Default no-op policy that mirrors LiteLLM hook signatures and does nothing.

Users can implement their own policies by providing the same methods and
setting the LUTHIEN_POLICY env var to "module.path:ClassName".
"""

from typing import Mapping, Optional

from luthien_proxy.types import JSONObject, JSONValue

from .base import LuthienPolicy


class NoOpPolicy(LuthienPolicy):
    """Policy that intentionally performs no modifications."""

    def __init__(self, options: Mapping[str, JSONValue] | None = None) -> None:
        """Initialise with optional configuration, forwarded to the base policy."""
        super().__init__(options=options)

    async def async_post_call_success_hook(
        self,
        data: JSONObject,
        user_api_key_dict: Optional[JSONObject],
        response: JSONObject,
    ) -> Optional[JSONObject]:
        """Return the unmodified final response for non-streaming calls."""
        return response
