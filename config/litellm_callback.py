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

    def log_pre_api_call(self, model, messages, kwargs):
        """Synchronous pre-call hook: forward to control plane.

        We don't attempt to rewrite the outgoing request here (LiteLLM's
        logger API is primarily observational), but we do invoke the
        control plane for visibility and future decisioning.
        """
        try:
            payload = {
                "user_api_key_dict": self._serialize_dict(
                    kwargs.get("user_api_key_dict")
                ),
                "cache": self._json_safe(kwargs.get("cache")),
                "data": self._json_safe(
                    {
                        **({} if kwargs is None else dict(kwargs)),
                        "model": model,
                        "messages": messages,
                    }
                ),
                "call_type": kwargs.get("call_type"),
            }
            url = f"{self.control_plane_url}/hooks/pre"
            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(url, json=payload)
                verbose_logger.debug(
                    f"LUTHIEN hook_pre status={res.status_code} body={res.text[:200]}"
                )
            # generic hook endpoint
            self._post_hook("log_pre_api_call", kwargs, None)
        except Exception as e:
            verbose_logger.debug(f"LUTHIEN hook_pre error: {e}")
        return super().log_pre_api_call(model, messages, kwargs)

    def log_post_api_call(self, kwargs, response_obj, start_time, end_time):
        # generic hook endpoint
        self._post_hook("log_post_api_call", kwargs, response_obj)
        return super().log_post_api_call(kwargs, response_obj, start_time, end_time)

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._post_hook("log_success_event", kwargs, response_obj)

    def log_failure_event(self, kwargs, response_obj, start_time, end_time):
        self._post_hook("log_failure_event", kwargs, response_obj)
        return super().log_failure_event(kwargs, response_obj, start_time, end_time)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        """Forward post-success to control plane for possible replacement/logging."""
        await self._apost_hook("async_log_success_event", kwargs, response_obj)
        try:
            payload = {
                "data": self._json_safe(kwargs or {}),
                "user_api_key_dict": self._serialize_dict(
                    kwargs.get("user_api_key_dict")
                    if isinstance(kwargs, dict)
                    else None
                ),
                "response": self._json_safe(self._serialize_response(response_obj)),
            }
            url = f"{self.control_plane_url}/hooks/post_success"
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.post(url, json=payload)
                verbose_logger.debug(
                    f"LUTHIEN hook_post_success status={res.status_code} body={res.text[:200]}"
                )
        except Exception as e:
            verbose_logger.debug(f"LUTHIEN hook_post_success error: {e}")

    async def async_post_call_success_hook(self, data, user_api_key_dict, response):
        """Proxy-only success hook: forward to control plane.

        Many proxy code paths call this instead of async_log_success_event.
        """
        try:
            payload = {
                "data": self._json_safe(data or {}),
                "user_api_key_dict": self._serialize_dict(user_api_key_dict),
                "response": self._json_safe(self._serialize_response(response)),
            }
            url = f"{self.control_plane_url}/hooks/post_success"
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.post(url, json=payload)
                verbose_logger.debug(
                    f"LUTHIEN post_success(proxy) status={res.status_code} body={res.text[:200]}"
                )
        except Exception as e:
            verbose_logger.debug(f"LUTHIEN post_success(proxy) error: {e}")
        return None

    async def async_log_stream_event(self, kwargs, response_obj, start_time, end_time):
        """Called during streaming; forward per-chunk to control plane."""
        await self._apost_hook("async_log_stream_event", kwargs, response_obj)
        return None

    def log_stream_event(self, kwargs, response_obj, start_time, end_time):
        """Sync variant for streaming chunks (some LiteLLM paths call this)."""
        self._post_hook("log_stream_event", kwargs, response_obj)
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

    async def async_log_pre_api_call(self, model, messages, kwargs):
        await self._apost_hook("async_log_pre_api_call", kwargs, None)
        return None

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        await self._apost_hook(
            "async_log_failure_event",
            kwargs,
            response_obj,
        )
        return None

    # ---------- Additional hooks for exhaustive tracing ----------

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

    async def async_post_call_streaming_hook(self, user_api_key_dict, response: str):
        await self._apost_hook(
            "async_post_call_streaming_hook",
            {"user_api_key_dict": self._serialize_dict(user_api_key_dict)},
            response,
        )
        return None

    async def async_pre_routing_hook(
        self,
        model: str,
        request_kwargs: dict,
        messages=None,
        input=None,
        specific_deployment: bool = False,
    ):
        await self._apost_hook(
            "async_pre_routing_hook",
            {
                "model": model,
                "request_kwargs": self._json_safe(request_kwargs),
                "messages": self._json_safe(messages),
                "input": self._json_safe(input),
                "specific_deployment": specific_deployment,
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

    async def async_pre_call_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        data: dict,
        call_type: str,
    ):
        await self._apost_hook(
            "async_pre_call_hook",
            {"data": self._json_safe(data), "call_type": call_type},
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

    async def async_logging_hook(self, kwargs: dict, result, call_type: str):
        await self._apost_hook(
            "async_logging_hook",
            {"kwargs": self._json_safe(kwargs), "call_type": call_type},
            result,
        )
        return kwargs, result

    def logging_hook(self, kwargs: dict, result, call_type: str):
        self._post_hook(
            "logging_hook",
            {"kwargs": self._json_safe(kwargs), "call_type": call_type},
            result,
        )
        return kwargs, result

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

    def log_event(self, *args, **kwargs):
        self._post_hook(
            "log_event",
            {"args": self._json_safe(args), "kwargs": self._json_safe(kwargs)},
            None,
        )

    async def async_log_event(self, *args, **kwargs):
        await self._apost_hook(
            "async_log_event",
            {"args": self._json_safe(args), "kwargs": self._json_safe(kwargs)},
            None,
        )

    def log_input_event(self, *args, **kwargs):
        self._post_hook(
            "log_input_event",
            {"args": self._json_safe(args), "kwargs": self._json_safe(kwargs)},
            None,
        )

    async def async_log_input_event(self, *args, **kwargs):
        await self._apost_hook(
            "async_log_input_event",
            {"args": self._json_safe(args), "kwargs": self._json_safe(kwargs)},
            None,
        )

    def log_model_group_rate_limit_error(
        self, exception: Exception, original_model_group: str | None, kwargs: dict
    ):
        self._post_hook(
            "log_model_group_rate_limit_error",
            {
                "original_model_group": original_model_group,
                "kwargs": self._json_safe(kwargs),
            },
            str(exception),
        )

    async def log_success_fallback_event(
        self, original_model_group: str, kwargs: dict, original_exception: Exception
    ):
        await self._apost_hook(
            "log_success_fallback_event",
            {
                "original_model_group": original_model_group,
                "kwargs": self._json_safe(kwargs),
            },
            str(original_exception),
        )

    async def log_failure_fallback_event(
        self, original_model_group: str, kwargs: dict, original_exception: Exception
    ):
        await self._apost_hook(
            "log_failure_fallback_event",
            {
                "original_model_group": original_model_group,
                "kwargs": self._json_safe(kwargs),
            },
            str(original_exception),
        )

    def translate_completion_input_params(self, kwargs) -> None:
        self._post_hook(
            "translate_completion_input_params",
            {"kwargs": self._json_safe(kwargs)},
            None,
        )
        return None

    def translate_completion_output_params(self, response) -> None:
        self._post_hook("translate_completion_output_params", {}, response)
        return None

    def translate_completion_output_params_streaming(self, completion_stream) -> None:
        self._post_hook("translate_completion_output_params_streaming", {}, None)
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
