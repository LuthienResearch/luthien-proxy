from __future__ import annotations

import os
import time
from typing import Any

import httpx
from litellm.integrations.custom_logger import CustomLogger
from litellm._logging import verbose_logger


class DebugCallback(CustomLogger):
    def __init__(self):
        super().__init__()
        self.control_plane_url = os.getenv(
            "CONTROL_PLANE_URL", "http://control-plane:8081"
        )
        self.timeout = 10.0

    def _safe(self, obj: Any) -> Any:
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
        when: str,
        kwargs: Any,
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        payload = {
            "hook": hook,
            "when": when,
            "t0": start_time if isinstance(start_time, (int, float)) else time.time(),
            "t1": end_time if isinstance(end_time, (int, float)) else time.time(),
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
        when: str,
        kwargs: Any,
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        payload = {
            "hook": hook,
            "when": when,
            "t0": start_time if isinstance(start_time, (int, float)) else time.time(),
            "t1": end_time if isinstance(end_time, (int, float)) else time.time(),
            "kwargs": self._safe(kwargs or {}),
            "response_obj": self._safe(self._serialize_response(response_obj)),
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                await client.post(
                    f"{self.control_plane_url}/api/hooks/log", json=payload
                )
        except Exception as e:
            verbose_logger.debug(f"DEBUG-CB apost error: {e}")

    def log_pre_api_call(self, model, messages, kwargs):
        self._post("log_pre_api_call", "pre", kwargs, None, None, None)

    def log_post_api_call(self, kwargs, response_obj, start_time, end_time):
        self._post(
            "log_post_api_call", "post", kwargs, response_obj, start_time, end_time
        )

    def log_stream_event(self, kwargs, response_obj, start_time, end_time):
        self._post(
            "log_stream_event", "stream", kwargs, response_obj, start_time, end_time
        )

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._post(
            "log_success_event", "success", kwargs, response_obj, start_time, end_time
        )

    def log_failure_event(self, kwargs, response_obj, start_time, end_time):
        self._post(
            "log_failure_event", "failure", kwargs, response_obj, start_time, end_time
        )

    async def async_log_pre_api_call(self, model, messages, kwargs):
        await self._apost("async_log_pre_api_call", "pre", kwargs, None, None, None)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        await self._apost(
            "async_log_success_event",
            "success",
            kwargs,
            response_obj,
            start_time,
            end_time,
        )

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        await self._apost(
            "async_log_failure_event",
            "failure",
            kwargs,
            response_obj,
            start_time,
            end_time,
        )

    async def async_log_stream_event(self, kwargs, response_obj, start_time, end_time):
        await self._apost(
            "async_log_stream_event",
            "stream",
            kwargs,
            response_obj,
            start_time,
            end_time,
        )

    async def async_on_stream_event(self, kwargs, response_obj, start_time, end_time):
        await self.async_log_stream_event(kwargs, response_obj, start_time, end_time)

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        await self._apost(
            "async_pre_call_hook",
            "pre",
            {
                "user_api_key_dict": user_api_key_dict,
                "cache": str(cache),
                "data": data,
                "call_type": call_type,
            },
            None,
            None,
            None,
        )
        return None

    async def async_post_call_success_hook(self, data, user_api_key_dict, response):
        await self._apost(
            "async_post_call_success_hook",
            "post",
            {"data": data, "user_api_key_dict": user_api_key_dict},
            response,
            None,
            None,
        )
        return None

    async def async_post_call_failure_hook(
        self, request_data, original_exception, user_api_key_dict, traceback_str=None
    ):
        await self._apost(
            "async_post_call_failure_hook",
            "failure",
            {
                "request_data": request_data,
                "user_api_key_dict": user_api_key_dict,
                "traceback": traceback_str,
            },
            str(original_exception),
            None,
            None,
        )

    async def async_post_call_streaming_hook(self, user_api_key_dict, response: str):
        await self._apost(
            "async_post_call_streaming_hook",
            "stream",
            {"user_api_key_dict": user_api_key_dict},
            response,
            None,
            None,
        )
        return None

    async def async_post_call_streaming_iterator_hook(
        self, user_api_key_dict, response, request_data: dict
    ):
        async for item in response:
            await self._apost(
                "async_post_call_streaming_iterator_hook",
                "stream",
                {"user_api_key_dict": user_api_key_dict, "request_data": request_data},
                item,
                None,
                None,
            )
            yield item


debug_callback = DebugCallback()
