"""Debug logger callback that forwards LiteLLM events to the control plane."""

from __future__ import annotations

from typing import Callable, Optional, cast

import httpx
from litellm._logging import verbose_proxy_logger
from litellm.integrations.custom_logger import CustomLogger

from luthien_proxy.control_plane.conversation.utils import json_safe
from luthien_proxy.types import JSONObject, JSONValue
from luthien_proxy.utils.project_config import ProjectConfig


class DebugCallback(CustomLogger):
    """LiteLLM CustomLogger that mirrors events to the control plane."""

    def __init__(
        self,
        config: Optional[ProjectConfig] = None,
        client_factory: Callable[..., httpx.Client] | None = None,
        async_client_factory: Callable[..., httpx.AsyncClient] | None = None,
    ):
        """Initialize callback with control-plane URL and defaults."""
        super().__init__()
        verbose_proxy_logger.info("DebugCallback initialized")
        project_config = config or ProjectConfig()
        self.control_plane_url = project_config.control_plane_url
        self.timeout = 10.0
        self._client_factory = client_factory or httpx.Client
        self._async_client_factory = async_client_factory or httpx.AsyncClient

    def _safe(self, obj: object) -> JSONValue:
        """Recursively convert objects into JSON-serializable structures."""
        return json_safe(obj)

    def _serialize_response(self, resp: object) -> JSONValue:
        """Return a serializable representation of LiteLLM response objects."""
        if resp is None:
            return None
        if isinstance(resp, dict):
            return cast(JSONObject, json_safe(resp))
        if hasattr(resp, "model_dump"):
            model_dump = getattr(resp, "model_dump")
            if callable(model_dump):
                try:
                    return json_safe(model_dump())
                except Exception as e:
                    verbose_proxy_logger.debug(f"model_dump() failed: {e}, falling back to str()")
                    return json_safe(str(resp))
        if hasattr(resp, "dict"):
            dict_method = getattr(resp, "dict")
            if callable(dict_method):
                try:
                    return json_safe(dict_method())
                except Exception as e:
                    verbose_proxy_logger.debug(f"dict() failed: {e}, falling back to str()")
                    return json_safe(str(resp))
        verbose_proxy_logger.debug(f"Response has no model_dump or dict, type={type(resp)}")
        return json_safe(str(resp))

    def _post(
        self,
        hook: str,
        kwargs: object | None,
        response_obj: object,
    ) -> None:
        """Send a synchronous log payload to the control plane."""
        payload = {
            "hook": hook,
            "kwargs": self._safe({} if kwargs is None else kwargs),
            "response_obj": self._safe(self._serialize_response(response_obj)),
        }
        try:
            with self._client_factory(timeout=self.timeout) as client:
                client.post(f"{self.control_plane_url}/api/hooks/log", json=payload)
        except Exception as e:
            verbose_proxy_logger.debug(f"DEBUG-CB post error: {e}")

    async def _apost(
        self,
        hook: str,
        kwargs: object | None,
        response_obj: object,
    ) -> None:
        """Send an async log payload to the control plane."""
        payload = {
            "hook": hook,
            "kwargs": self._safe({} if kwargs is None else kwargs),
            "response_obj": self._safe(self._serialize_response(response_obj)),
        }
        try:
            async with self._async_client_factory(timeout=self.timeout) as client:
                await client.post(f"{self.control_plane_url}/api/hooks/log", json=payload)
        except Exception as e:
            verbose_proxy_logger.debug(f"DEBUG-CB apost error: {e}")

    def log_pre_api_call(self, model, messages, kwargs):
        """Log pre-call data for a non-async invocation."""
        self._post("log_pre_api_call", kwargs, None)

    def log_post_api_call(self, kwargs, response_obj, start_time, end_time):
        """Log post-call data for a non-async invocation."""
        self._post("log_post_api_call", kwargs, response_obj)

    def log_stream_event(self, kwargs, response_obj, start_time, end_time):
        """Log a streaming event for a non-async invocation."""
        self._post("log_stream_event", kwargs, response_obj)

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        """Log a success event for a non-async invocation."""
        self._post("log_success_event", kwargs, response_obj)

    def log_failure_event(self, kwargs, response_obj, start_time, end_time):
        """Log a failure event for a non-async invocation."""
        self._post("log_failure_event", kwargs, response_obj)

    async def async_log_pre_api_call(self, model, messages, kwargs):
        """Log pre-call data for an async invocation."""
        await self._apost("async_log_pre_api_call", kwargs, None)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        """Log a success event for an async invocation."""
        await self._apost("async_log_success_event", kwargs, response_obj)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        """Log a failure event for an async invocation."""
        await self._apost("async_log_failure_event", kwargs, response_obj)

    async def async_log_stream_event(self, kwargs, response_obj, start_time, end_time):
        """Log a streaming event for an async invocation."""
        await self._apost("async_log_stream_event", kwargs, response_obj)

    async def async_on_stream_event(self, kwargs, response_obj, start_time, end_time):
        """Compatibility wrapper mapping to async_log_stream_event."""
        await self.async_log_stream_event(kwargs, response_obj, start_time, end_time)

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        """Forward LiteLLM async_pre_call_hook payload to control plane."""
        await self._apost(
            "async_pre_call_hook",
            {
                "user_api_key_dict": user_api_key_dict,
                "cache": str(cache),
                "data": data,
                "call_type": call_type,
            },
            None,
        )
        return None

    async def async_post_call_success_hook(self, data, user_api_key_dict, response):
        """Forward async_post_call_success_hook with final response."""
        await self._apost(
            "async_post_call_success_hook",
            {"data": data, "user_api_key_dict": user_api_key_dict},
            response,
        )
        return None

    async def async_post_call_failure_hook(
        self, request_data, original_exception, user_api_key_dict, traceback_str=None
    ):
        """Forward async_post_call_failure_hook with exception details."""
        await self._apost(
            "async_post_call_failure_hook",
            {
                "request_data": request_data,
                "user_api_key_dict": user_api_key_dict,
                "traceback": traceback_str,
            },
            str(original_exception),
        )

    async def async_post_call_streaming_hook(self, user_api_key_dict, response: str):
        """Forward async_post_call_streaming_hook with aggregate stream info."""
        await self._apost(
            "async_post_call_streaming_hook",
            {"user_api_key_dict": user_api_key_dict},
            response,
        )
        return None

    async def async_post_call_streaming_iterator_hook(self, user_api_key_dict, response, request_data: dict):
        """Wrap the streaming iterator and mirror per-chunk events."""
        async for item in response:
            await self._apost(
                "async_post_call_streaming_iterator_hook",
                {"user_api_key_dict": user_api_key_dict, "request_data": request_data},
                item,
            )
            yield item


debug_callback = DebugCallback()
