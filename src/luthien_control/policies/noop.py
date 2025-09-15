"""
Default no-op policy that mirrors LiteLLM hook signatures and does nothing.

Users can implement their own policies by providing the same methods and
setting the LUTHIEN_POLICY env var to "module.path:ClassName".
"""

from typing import Any, AsyncGenerator, Dict, Optional

from litellm.integrations.custom_logger import CustomLogger


class NoOpPolicy(CustomLogger):
    async def async_pre_call_hook(
        self,
        user_api_key_dict: Optional[Dict[str, Any]],
        cache: Optional[Dict[str, Any]],
        data: Dict[str, Any],
        call_type: Optional[str],
    ) -> Optional[object]:
        # Pass-through: return None to indicate "no change"
        return None

    async def async_post_call_success_hook(
        self,
        data: Dict[str, Any],
        user_api_key_dict: Optional[Dict[str, Any]],
        response: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        # Keep original response
        return None

    async def streaming_decide(
        self,
        user_api_key_dict: Optional[Dict[str, Any]],
        request_data: Dict[str, Any],
    ) -> bool:
        # Never replace the stream by default
        return False

    async def streaming_replacement(
        self,
        request_data: Dict[str, Any],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        # No replacement stream
        if False:
            yield {}
        return

    async def streaming_on_chunk(
        self,
        user_api_key_dict: Optional[Dict[str, Any]],
        request_data: Dict[str, Any],
        chunk: Dict[str, Any],
        chunk_index: int,
        accumulated_text: str,
    ) -> Dict[str, Any]:
        # Always pass original chunk through unchanged
        return {"action": "pass"}
