# ABOUTME: Minimal callback skeleton that LiteLLM can load directly
# ABOUTME: Acts as a thin proxy forwarding all calls to the control plane

"""
Minimal LiteLLM callback that forwards all calls to the control plane.
This module is loaded by LiteLLM via the `callbacks` entry in
`config/litellm_config.yaml`.

Implements a thin bridge:
- All hooks are forwarded via `POST /hooks/{hook_name}` (fire-and-forget)
- Streaming iterator events are wrapped to forward each chunk through the same path
"""

import os
import time as _time
from typing import Any, AsyncGenerator, Optional, Union
from litellm.integrations.custom_logger import CustomLogger
from litellm._logging import verbose_logger

import httpx


class LuthienCallback(CustomLogger):
    """Thin callback that forwards everything to the control plane."""

    def __init__(self):
        super().__init__()
        self.control_plane_url = os.getenv(
            "CONTROL_PLANE_URL", "http://control-plane:8081"
        )
        self.timeout = 10.0
        verbose_logger.info(
            f"LUTHIEN LuthienCallback initialized with control plane URL: {self.control_plane_url}"
        )

    # ------------- internal helpers -------------
    def _post_hook(
        self,
        hook: str,
        payload: dict,
    ) -> Any:
        try:
            payload["post_time_ns"] = _time.time_ns()
            with httpx.Client(timeout=self.timeout) as client:
                luthien_response: httpx.Response = client.post(
                    f"{self.control_plane_url}/hooks/{hook}",
                    json=self._json_safe(payload),
                )
            return luthien_response.json()
        except Exception as e:
            verbose_logger.error(f"LUTHIEN hook post error ({hook}): {e}")

    async def _apost_hook(
        self,
        hook: str,
        payload: dict,
    ) -> Any:
        try:
            payload["post_time_ns"] = _time.time_ns()
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                await client.post(
                    f"{self.control_plane_url}/hooks/{hook}",
                    json=self._json_safe(payload),
                )
        except Exception as e:
            verbose_logger.error(f"LUTHIEN hook post error ({hook}): {e}")
            raise e

    # --------------------- Hooks ----------------------

    async def async_pre_call_hook(
        self, **kwargs
    ) -> Optional[Union[Exception, str, dict]]:
        await self._apost_hook(
            "async_pre_call_hook",
            self._json_safe(kwargs),
        )

    async def async_post_call_failure_hook(self, **kwargs):
        await self._apost_hook(
            "async_post_call_failure_hook",
            self._json_safe(kwargs),
        )
        return None

    async def async_post_call_success_hook(self, **kwargs):
        """Allow control plane to replace final response for non-streaming calls."""
        await self._apost_hook(
            "async_post_call_success_hook",
            self._json_safe(kwargs),
        )

    async def async_moderation_hook(self, **kwargs):
        await self._apost_hook(
            "async_moderation_hook",
            self._json_safe(kwargs),
        )
        return None

    async def async_post_call_streaming_hook(self, **kwargs):
        await self._apost_hook(
            "async_post_call_streaming_hook",
            self._json_safe(kwargs),
        )
        return None

    async def async_post_call_streaming_iterator_hook(
        self, user_api_key_dict, response, request_data: dict
    ) -> AsyncGenerator[Any, None]:
        """Wrap the streaming iterator to allow per-chunk edits or suppression."""
        try:
            async for item in response:
                await self._apost_hook(
                    "async_post_call_streaming_iterator_hook",
                    {
                        "user_api_key_dict": self._json_safe(user_api_key_dict),
                        "response": self._serialize_response(item),
                        "request_data": self._json_safe(request_data),
                    },
                )
                # Default: pass original item
                yield item
        except Exception as e:
            verbose_logger.error(
                f"LUTHIEN async_post_call_streaming_iterator_hook error: {e}"
            )
            # If wrapping fails, yield from original response to avoid breaking stream
            async for item in response:
                yield item
        finally:
            return

    # Fallback for LiteLLM versions that emit async_on_stream_event instead of iterator hook
    async def async_on_stream_event(
        self, kwargs, response_obj, start_time, end_time
    ) -> None:
        try:
            request_data = None
            user_api_key_dict = None
            try:
                if isinstance(kwargs, dict):
                    request_data = (
                        kwargs.get("request_data")
                        or kwargs.get("data")
                        or kwargs.get("kwargs")
                    )
                    user_api_key_dict = kwargs.get("user_api_key_dict")
            except Exception:
                request_data = None
                user_api_key_dict = None

            await self._apost_hook(
                "async_post_call_streaming_iterator_hook",
                {
                    "user_api_key_dict": self._json_safe(user_api_key_dict),
                    "response": self._serialize_response(response_obj),
                    "request_data": self._json_safe(request_data)
                    if request_data
                    else {},
                },
            )
        except Exception as e:
            verbose_logger.error(f"LUTHIEN async_on_stream_event forward error: {e}")
            return None

    def _serialize_dict(self, obj):
        """Safely serialize objects to dict."""
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj
        # Try to extract attributes for user_api_key_dict objects
        result = {}
        for attr in ["user_id", "team_id", "email", "org_id"]:
            val = getattr(obj, attr, None)
            if val is not None:
                result[attr] = val
        return result if result else None

    def _serialize_response(self, response):
        """Safely serialize response objects."""
        if response is None:
            return None
        if isinstance(response, dict):
            return response
        if hasattr(response, "model_dump"):
            return response.model_dump()
        return str(response)

    def _json_safe(self, obj):
        """Recursively convert objects into JSON-serializable structures.

        - Dicts/lists/tuples/sets: processed recursively
        - Basic scalars: returned as-is
        - Other objects: converted to string via repr()
        """
        try:
            import json as _json

            _json.dumps(obj)  # Fast path: already serializable
            return obj
        except Exception:
            pass

        # Recursive conversion
        if isinstance(obj, dict):
            return {self._json_safe(k): self._json_safe(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [self._json_safe(v) for v in obj]
        if isinstance(obj, (str, int, float, bool)) or obj is None:
            return obj
        # Fallback to repr for unknown objects
        try:
            return repr(obj)
        except Exception:
            return "<unserializable>"


# Create the singleton instance that LiteLLM will use
luthien_callback = LuthienCallback()
