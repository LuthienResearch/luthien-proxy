"""Callback that records LiteLLM events locally for replay and analysis."""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from litellm._logging import verbose_proxy_logger
from litellm.integrations.custom_logger import CustomLogger

from luthien_proxy.types import JSONObject

JsonLike = dict[str, Any]


class ReplayCallback(CustomLogger):
    """LiteLLM CustomLogger that writes hook events to a JSONL file."""

    _MAX_DEPTH = 6
    _MAX_ITEMS = 32
    _MAX_PREVIEW = 512

    def __init__(
        self,
        log_path: str | Path | None = None,
        writer: Callable[[JSONObject], None] | None = None,
    ) -> None:
        """Initialize callback with storage location and writer."""
        super().__init__()
        default_path = os.getenv("LUTHIEN_REPLAY_LOG_PATH", "dev/replay_logs.jsonl")
        self._log_path = Path(log_path or default_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._writer = writer or self._write_jsonl
        verbose_proxy_logger.info(f"ReplayCallback writing events to {self._log_path}")

    def _capture(self, obj: Any, *, depth: int = 0) -> JsonLike:
        """Capture object structure with type metadata for later analysis."""
        if depth >= self._MAX_DEPTH:
            return {
                "type": type(obj).__name__,
                "module": getattr(type(obj), "__module__", None),
                "repr": repr(obj)[: self._MAX_PREVIEW],
                "truncated": True,
            }

        if obj is None:
            return {"type": "NoneType", "value": None}

        if isinstance(obj, (bool, int, float, str)):
            return {"type": type(obj).__name__, "value": obj}

        if isinstance(obj, bytes):
            return {
                "type": "bytes",
                "encoding": "base64",
                "length": len(obj),
                "data": base64.b64encode(obj).decode("ascii"),
            }

        if isinstance(obj, (list, tuple)):
            items = []
            for index, item in enumerate(obj):
                if index >= self._MAX_ITEMS:
                    items.append({"type": "__truncated__", "remaining": len(obj) - index})
                    break
                items.append(self._capture(item, depth=depth + 1))
            return {
                "type": type(obj).__name__,
                "length": len(obj),
                "items": items,
            }

        if isinstance(obj, set):
            items = []
            for index, item in enumerate(obj):
                if index >= self._MAX_ITEMS:
                    items.append({"type": "__truncated__", "remaining": len(obj) - index})
                    break
                items.append(self._capture(item, depth=depth + 1))
            return {"type": "set", "length": len(obj), "items": items}

        if isinstance(obj, dict):
            entries = []
            for index, (key, value) in enumerate(obj.items()):
                if index >= self._MAX_ITEMS:
                    entries.append({"type": "__truncated__", "remaining": len(obj) - index})
                    break
                entries.append(
                    {
                        "key": self._capture(key, depth=depth + 1),
                        "value": self._capture(value, depth=depth + 1),
                    }
                )
            return {"type": "dict", "length": len(obj), "entries": entries}

        if hasattr(obj, "model_dump"):
            try:
                model_dump = obj.model_dump()
            except Exception as exc:  # pragma: no cover - defensive
                verbose_proxy_logger.debug(f"model_dump() failed for {type(obj)}: {exc}")
                return {
                    "type": type(obj).__name__,
                    "module": type(obj).__module__,
                    "repr": repr(obj)[: self._MAX_PREVIEW],
                    "model_dump_error": str(exc),
                }
            return {
                "type": type(obj).__name__,
                "module": type(obj).__module__,
                "is_pydantic": True,
                "model_dump": self._capture(model_dump, depth=depth + 1),
            }

        return self._capture_generic(obj)

    def _capture_generic(self, obj: Any) -> JsonLike:
        """Fallback capture including repr preview."""
        return {
            "type": type(obj).__name__,
            "module": type(obj).__module__,
            "repr": repr(obj)[: self._MAX_PREVIEW],
        }

    def _capture_payload(self, obj: Any, *, depth: int = 0) -> JsonLike:
        """Capture payload including fallback metadata."""
        captured = self._capture(obj, depth=depth)
        if "type" not in captured:
            # If _capture didn't recognise the object, fallback to generic repr.
            captured = self._capture_generic(obj)
        return captured

    def _write_jsonl(self, event: JSONObject) -> None:
        """Append an event to the replay log file."""
        try:
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception as exc:  # pragma: no cover - defensive
            verbose_proxy_logger.debug(f"ReplayCallback write error: {exc}")

    def _record(self, hook: str, kwargs: object | None, response_obj: object) -> None:
        """Build and persist a replay event."""
        event: JSONObject = {
            "hook": hook,
            "timestamp_ns": time.time_ns(),
            "kwargs": self._capture_payload(kwargs),
            "response_obj": self._capture_payload(response_obj),
        }
        self._writer(event)

    def _post(
        self,
        hook: str,
        kwargs: object | None,
        response_obj: object,
    ) -> None:
        """Record a synchronous log payload."""
        self._record(hook, kwargs, response_obj)

    async def _apost(
        self,
        hook: str,
        kwargs: object | None,
        response_obj: object,
    ) -> None:
        """Record an async log payload."""
        self._record(hook, kwargs, response_obj)

    def log_pre_api_call(self, model, messages, kwargs):  # noqa: ANN001
        """Log pre-call data for a non-async invocation."""
        self._post("log_pre_api_call", kwargs, None)

    def log_post_api_call(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
        """Log post-call data for a non-async invocation."""
        self._post("log_post_api_call", kwargs, response_obj)

    def log_stream_event(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
        """Log a streaming event for a non-async invocation."""
        self._post("log_stream_event", kwargs, response_obj)

    def log_success_event(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
        """Log a success event for a non-async invocation."""
        self._post("log_success_event", kwargs, response_obj)

    def log_failure_event(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
        """Log a failure event for a non-async invocation."""
        self._post("log_failure_event", kwargs, response_obj)

    async def async_log_pre_api_call(self, model, messages, kwargs):  # noqa: ANN001
        """Log pre-call data for an async invocation."""
        await self._apost("async_log_pre_api_call", kwargs, None)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
        """Log a success event for an async invocation."""
        await self._apost("async_log_success_event", kwargs, response_obj)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
        """Log a failure event for an async invocation."""
        await self._apost("async_log_failure_event", kwargs, response_obj)

    async def async_log_stream_event(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
        """Log a streaming event for an async invocation."""
        await self._apost("async_log_stream_event", kwargs, response_obj)

    async def async_on_stream_event(self, kwargs, response_obj, start_time, end_time):  # noqa: ANN001
        """Compatibility wrapper mapping to async_log_stream_event."""
        await self.async_log_stream_event(kwargs, response_obj, start_time, end_time)

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):  # noqa: ANN001
        """Forward LiteLLM async_pre_call_hook payload for replay."""
        await self._apost(
            "async_pre_call_hook",
            {
                "user_api_key_dict": user_api_key_dict,
                "cache": cache,
                "data": data,
                "call_type": call_type,
            },
            None,
        )
        return None

    async def async_post_call_success_hook(self, data, user_api_key_dict, response):  # noqa: ANN001
        """Record async_post_call_success_hook with final response."""
        await self._apost(
            "async_post_call_success_hook",
            {"data": data, "user_api_key_dict": user_api_key_dict},
            response,
        )
        return None

    async def async_post_call_failure_hook(  # noqa: ANN001
        self,
        request_data,
        original_exception,
        user_api_key_dict,
        traceback_str=None,
    ):
        """Record async_post_call_failure_hook with exception details."""
        await self._apost(
            "async_post_call_failure_hook",
            {
                "request_data": request_data,
                "user_api_key_dict": user_api_key_dict,
                "traceback": traceback_str,
            },
            str(original_exception),
        )

    async def async_post_call_streaming_hook(self, user_api_key_dict, response):  # noqa: ANN001
        """Record async_post_call_streaming_hook with aggregate stream info."""
        await self._apost(
            "async_post_call_streaming_hook",
            {"user_api_key_dict": user_api_key_dict},
            response,
        )
        return None

    async def async_post_call_streaming_iterator_hook(  # noqa: ANN001
        self,
        user_api_key_dict,
        response,
        request_data,
    ):
        """Wrap the streaming iterator and mirror per-chunk events."""
        async for item in response:
            await self._apost(
                "async_post_call_streaming_iterator_hook",
                {"user_api_key_dict": user_api_key_dict, "request_data": request_data},
                item,
            )
            yield item


replay_callback = ReplayCallback()
