"""Debug logger callback that forwards LiteLLM events to the control plane."""

from __future__ import annotations

import os
from typing import Any

import httpx
from litellm._logging import verbose_logger
from litellm.integrations.custom_logger import CustomLogger


class DebugCallback(CustomLogger):
    """LiteLLM CustomLogger that mirrors events to the control plane."""

    def __init__(self):
        """Initialize callback with control-plane URL and defaults."""
        super().__init__()
        self.control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8081")
        self.timeout = 10.0

    def _safe(self, obj: Any) -> Any:
        """Recursively convert objects into JSON-serializable structures."""
        try:
            import json

            json.dumps(obj)
            return obj
        except Exception:
            pass
        if isinstance(obj, dict):
            return {self._safe(k): self._safe(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [self._safe(v) for v in obj]
        if isinstance(obj, (str, int, float, bool)) or obj is None:
            return obj
        try:
            return repr(obj)
        except Exception:
            return "<unserializable>"

    def _serialize_response(self, resp: Any) -> Any:
        """Return a serializable representation of LiteLLM response objects."""
        if resp is None:
            return None
        if isinstance(resp, dict):
            return resp
        if hasattr(resp, "model_dump"):
            try:
                return resp.model_dump()
            except Exception:
                return str(resp)
        return str(resp)

    def _post(
        self,
        hook: str,
        kwargs: Any,
        response_obj: Any,
    ) -> None:
        """Send a synchronous log payload to the control plane."""
        payload = {
            "hook": hook,
            "kwargs": self._safe(kwargs or {}),
            "response_obj": self._safe(self._serialize_response(response_obj)),
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                client.post(f"{self.control_plane_url}/api/hooks/log", json=payload)
        except Exception as e:
            verbose_logger.debug(f"DEBUG-CB post error: {e}")

    async def _apost(
        self,
        hook: str,
        kwargs: Any,
        response_obj: Any,
    ) -> None:
        """Send an async log payload to the control plane."""
        payload = {
            "hook": hook,
            "kwargs": self._safe(kwargs or {}),
            "response_obj": self._safe(self._serialize_response(response_obj)),
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                await client.post(f"{self.control_plane_url}/api/hooks/log", json=payload)
        except Exception as e:
            verbose_logger.debug(f"DEBUG-CB apost error: {e}")

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
