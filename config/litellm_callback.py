# ABOUTME: Minimal callback skeleton that LiteLLM can load directly
# ABOUTME: Acts as a thin proxy forwarding all calls to the control plane

"""
Minimal LiteLLM callback that forwards all calls to the control plane.
This is what LiteLLM loads directly from the config file.
"""

import os
from litellm.integrations.custom_logger import CustomLogger
from litellm._logging import verbose_logger


class LuthienCallback(CustomLogger):
    """Thin callback that forwards everything to the control plane."""

    def __init__(self):
        super().__init__()
        self.control_plane_url = os.getenv(
            "CONTROL_PLANE_URL", "http://control-plane:8081"
        )
        self.timeout = 10.0
        verbose_logger.debug(
            f"LUTHIEN LuthienCallback initialized with control plane URL: {self.control_plane_url}"
        )

    def log_pre_api_call(self, model, messages, kwargs):
        return super().log_pre_api_call(model, messages, kwargs)

    def log_post_api_call(self, kwargs, response_obj, start_time, end_time):
        return super().log_post_api_call(kwargs, response_obj, start_time, end_time)

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        """Synchronous version - just verbose_logger.debug for debugging."""
        verbose_logger.debug("LUTHIEN SYNC SUCCESS EVENT CALLED")

    def log_failure_event(self, kwargs, response_obj, start_time, end_time):
        return super().log_failure_event(kwargs, response_obj, start_time, end_time)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        """No-op: /hooks endpoints removed; avoid posting to control plane.

        If needed, future instrumentation can write directly to a new
        ingestion endpoint or another sink.
        """
        verbose_logger.debug("LUTHIEN ASYNC SUCCESS EVENT - no-op (hooks disabled)")

    async def async_log_stream_event(self, kwargs, response_obj, start_time, end_time):
        """Called during streaming - can modify or log stream chunks."""
        # For MVP, just pass through
        pass

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        """Called on API failure."""
        verbose_logger.debug(f"LUTHIEN FAILURE EVENT - Error: {response_obj}")
        pass

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
