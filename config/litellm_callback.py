# ABOUTME: Minimal callback skeleton that LiteLLM can load directly
# ABOUTME: Acts as a thin proxy forwarding all calls to the control plane

"""
Minimal LiteLLM callback that forwards all calls to the control plane.
This module is loaded by LiteLLM via the `callbacks` entry in
`config/litellm_config.yaml`.

Implements a thin bridge:
- pre: POST /hooks/pre (sync)
- post-success: POST /hooks/post_success (async)
- streaming: generic `/hooks/{hook_name}` forwarding for iterator/log hooks
"""

import os
from typing import Optional, Union
from litellm.caching.caching import DualCache
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy._types import UserAPIKeyAuth
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
        kwargs,
        response_obj,
    ):
        try:
            import time as _time

            with httpx.Client(timeout=self.timeout) as client:
                client.post(
                    f"{self.control_plane_url}/hooks/{hook}",
                    json={
                        "post_time_ns": _time.time_ns(),
                        "kwargs": self._json_safe(kwargs or {}),
                        "response_obj": self._json_safe(
                            self._serialize_response(response_obj)
                        ),
                    },
                )
        except Exception as e:
            verbose_logger.debug(f"LUTHIEN hook post error ({hook}): {e}")

    async def _apost_hook(
        self,
        hook: str,
        kwargs,
        response_obj,
    ):
        try:
            import time as _time

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                await client.post(
                    f"{self.control_plane_url}/hooks/{hook}",
                    json={
                        "post_time_ns": _time.time_ns(),
                        "kwargs": self._json_safe(kwargs or {}),
                        "response_obj": self._json_safe(
                            self._serialize_response(response_obj)
                        ),
                    },
                )
        except Exception as e:
            verbose_logger.debug(f"LUTHIEN hook post error ({hook}): {e}")

    # --------------------- Hooks ----------------------

    async def async_pre_call_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        cache: DualCache,
        data: dict,
        call_type: str,
    ) -> Optional[Union[Exception, str, dict]]:
        await self._apost_hook(
            "async_pre_call_hook",
            {"data": self._json_safe(data), "call_type": call_type},
            None,
        )
        pass

    async def async_post_call_failure_hook(
        self, request_data, original_exception, user_api_key_dict, traceback_str=None
    ):
        await self._apost_hook(
            "async_post_call_failure_hook",
            {
                "request_data": self._json_safe(request_data),
                "user_api_key_dict": self._serialize_dict(user_api_key_dict),
                "traceback": traceback_str,
            },
            str(original_exception),
        )
        return None

    async def async_post_call_success_hook(self, data, user_api_key_dict, response):
        """Proxy-only success hook: forward to control plane.

        Many proxy code paths call this instead of async_log_success_event.
        """
        await self._apost_hook(
            "async_post_call_success_hook",
            {
                "data": self._json_safe(data or {}),
                "user_api_key_dict": self._serialize_dict(user_api_key_dict),
                "response": self._json_safe(self._serialize_response(response)),
            },
            None,
        )
        return None

    async def async_post_call_streaming_hook(self, user_api_key_dict, response: str):
        await self._apost_hook(
            "async_post_call_streaming_hook",
            {"user_api_key_dict": self._serialize_dict(user_api_key_dict)},
            response,
        )
        return None

    async def async_post_call_streaming_iterator_hook(
        self, user_api_key_dict, response, request_data: dict
    ):
        """Wrap the streaming iterator to forward each chunk to control plane."""
        try:
            async for item in response:
                await self._apost_hook(
                    "async_post_call_streaming_iterator_hook",
                    {
                        "user_api_key_dict": self._serialize_dict(user_api_key_dict),
                        "request_data": request_data,
                    },
                    item,
                )
                yield item
        except Exception as e:
            verbose_logger.debug(
                f"LUTHIEN async_post_call_streaming_iterator_hook error: {e}"
            )
            # If wrapping fails, yield from original response to avoid breaking stream
            async for item in response:
                yield item
        finally:
            return

    async def async_moderation_hook(
        self, data: dict, user_api_key_dict, call_type: str
    ):
        await self._apost_hook(
            "async_moderation_hook",
            {
                "data": self._json_safe(data),
                "call_type": call_type,
                "user_api_key_dict": self._serialize_dict(user_api_key_dict),
            },
            None,
        )
        return None

    async def async_pre_call_deployment_hook(self, kwargs: dict, call_type: str):
        """
        Use this instead of 'async_pre_call_hook' when you need to modify the request AFTER a deployment is selected, but BEFORE the request is sent.
        """
        await self._apost_hook(
            "async_pre_call_deployment_hook",
            {"kwargs": self._json_safe(kwargs), "call_type": call_type},
            None,
        )
        return None

    async def async_post_call_success_deployment_hook(
        self, request_data: dict, response, call_type
    ):
        await self._apost_hook(
            "async_post_call_success_deployment_hook",
            {"request_data": self._json_safe(request_data), "call_type": call_type},
            response,
        )
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
