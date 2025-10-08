# ABOUTME: LiteLLM callback that emits provider-agnostic payloads.
# ABOUTME: Normalizes Anthropic streams to OpenAI-style chunk format.

"""LiteLLM callback that forwards canonicalised payloads to the control plane.

This callback mirrors ``litellm_callback.py`` but adds one key behaviour: all
streaming data that reaches the control plane (and the client) uses the same
OpenAI Chat Completions chunk schema. Anthropic's SSE stream is converted into
OpenAI-style chunks via :mod:`luthien_proxy.proxy.stream_normalization`, so the
control plane never needs provider-specific logic.

Canonical streaming chunk (``dict``) delivered to the control plane::

    {
        "id": "chatcmpl-...",  # upstream chunk identifier
        "model": "gpt-5",  # OpenAI-style model name
        "created": 1710000000,  # unix timestamp
        "object": "chat.completion.chunk",
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant" | None, "content": "partial text" | None, "tool_calls": [...] | None},
                "finish_reason": "stop" | "tool_calls" | None,
                "logprobs": None,
            }
        ],
    }

This matches OpenAI's API, even when the upstream model is Anthropic.
"""

from __future__ import annotations

import contextlib
import os
import time as _time
from typing import Any, AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping, Optional, Union, cast

import httpx
import pydantic
from litellm._logging import verbose_logger
from litellm.integrations.custom_logger import CustomLogger
from litellm.types.utils import ModelResponseStream

from luthien_proxy.proxy.callback_chunk_logger import get_callback_chunk_logger
from luthien_proxy.proxy.callback_instrumentation import instrument_callback
from luthien_proxy.proxy.stream_connection_manager import StreamConnection
from luthien_proxy.proxy.stream_normalization import AnthropicToOpenAIAdapter
from luthien_proxy.proxy.stream_orchestrator import (
    StreamOrchestrationError,
    StreamOrchestrator,
    StreamTimeoutError,
)


def _is_anthropic_model(model_name: str | None) -> bool:
    """Return ``True`` if *model_name* corresponds to an Anthropic backend."""
    if not model_name:
        return False
    lowered = model_name.lower()
    return "anthropic" in lowered or "claude" in lowered


class UnifiedCallback(CustomLogger):
    """LiteLLM callback that forwards provider-agnostic payloads."""

    def __init__(self) -> None:
        """Capture control-plane configuration and defaults."""
        super().__init__()
        self.control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8081")
        self.timeout = 10.0
        self.stream_timeout = float(os.getenv("CONTROL_PLANE_STREAM_TIMEOUT", "30"))

        if self.stream_timeout < 1.0:
            verbose_logger.error(
                "CONTROL_PLANE_STREAM_TIMEOUT=%s is below minimum (1.0s). This may cause premature timeouts.",
                self.stream_timeout,
            )
        elif self.stream_timeout > 600.0:
            verbose_logger.error(
                "CONTROL_PLANE_STREAM_TIMEOUT=%s exceeds maximum (600s). This may cause resource exhaustion.",
                self.stream_timeout,
            )

        verbose_logger.info("UnifiedCallback initialized with control plane URL: %s", self.control_plane_url)

    # ------------------------------------------------------------------
    # Control-plane HTTP helpers
    # ------------------------------------------------------------------
    async def _apost_hook(self, hook: str, payload: dict) -> Optional[dict]:
        """Send *payload* to ``/api/hooks/{hook}`` and return JSON response."""
        try:
            payload["post_time_ns"] = _time.time_ns()
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.control_plane_url}/api/hooks/{hook}",
                    json=self._json_safe(payload),
                )
                response.raise_for_status()

                content_type = response.headers.get("content-type", "")
                if "application/json" not in content_type:
                    raise httpx.HTTPError(f"Unexpected content-type from control plane for {hook} hook: {content_type}")

                if not response.content:
                    raise httpx.HTTPError(f"Empty response from control plane for {hook} hook")
                return response.json()
        except httpx.ConnectError as exc:  # pragma: no cover - network failure path
            verbose_logger.error("Network error posting %s hook: %s", hook, exc)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                verbose_logger.error("Control plane server error (%s hook): %s", hook, exc)
            else:
                verbose_logger.error("Client error posting %s hook: %s", hook, exc)
        except httpx.TimeoutException as exc:
            verbose_logger.error("Timeout posting %s hook: %s", hook, exc)
        except Exception as exc:  # pragma: no cover - defensive
            verbose_logger.error("Unexpected error posting %s hook: %s", hook, exc)
        return None

    def _apply_policy_response(self, response: Any, policy_result: Any) -> None:
        """Overwrite *response* in place with *policy_result* (dict or BaseModel)."""
        if response is None or policy_result is None:
            return

        if isinstance(policy_result, pydantic.BaseModel):
            payload: Mapping[str, Any] = policy_result.model_dump()
        elif isinstance(policy_result, Mapping):
            payload = policy_result
        else:  # pragma: no cover - defensive
            raise TypeError(f"Policy result must be Mapping or BaseModel, got {type(policy_result).__name__}")

        if isinstance(response, dict):
            response.clear()
            response.update(payload)
            return

        if isinstance(response, pydantic.BaseModel):
            prepared = dict(payload)
            prepared.pop("_source_type_", None)
            updated = response.__class__(**prepared)
            response.__dict__.clear()
            response.__dict__.update(updated.__dict__)
            if hasattr(updated, "__pydantic_extra__"):
                object.__setattr__(response, "__pydantic_extra__", getattr(updated, "__pydantic_extra__", None))
            if hasattr(updated, "__pydantic_fields_set__"):
                object.__setattr__(
                    response,
                    "__pydantic_fields_set__",
                    getattr(updated, "__pydantic_fields_set__", set()),
                )
            return

        raise TypeError(f"Unsupported response type: {type(response).__name__}")

    # ------------------------------------------------------------------
    # Hook implementations
    # ------------------------------------------------------------------
    @instrument_callback
    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: Any,
        data: dict,
        call_type: str,
    ) -> Optional[Union[Exception, str, dict]]:
        """Forward the LiteLLM ``async_pre_call_hook`` payload to the control plane."""
        await self._apost_hook(
            "async_pre_call_hook",
            {
                "user_api_key_dict": user_api_key_dict,
                "cache": cache,
                "data": data,
                "call_type": call_type,
            },
        )

    @instrument_callback
    async def async_post_call_failure_hook(
        self,
        request_data: dict,
        original_exception: Exception,
        user_api_key_dict: Any,
        traceback_str: Optional[str] = None,
    ) -> None:
        """Notify the control plane that the upstream call failed."""
        await self._apost_hook(
            "async_post_call_failure_hook",
            {
                "request_data": request_data,
                "original_exception": original_exception,
                "user_api_key_dict": user_api_key_dict,
                "traceback_str": traceback_str,
            },
        )

    @instrument_callback
    async def async_post_call_success_hook(self, data: dict, user_api_key_dict: Any, response: Any):
        """Let the control plane inspect and optionally replace the final response."""
        result = await self._apost_hook(
            "async_post_call_success_hook",
            {
                "data": data,
                "user_api_key_dict": user_api_key_dict,
                "response": response,
            },
        )

        if result is None:
            return result

        try:
            self._apply_policy_response(response, result)
        except Exception as exc:  # pragma: no cover - defensive
            verbose_logger.error("Failed to apply control plane response override: %s", exc)
        return result

    @instrument_callback
    async def async_moderation_hook(self, data: dict, user_api_key_dict: Any, call_type: str):
        """Forward moderation decisions to the control plane."""
        await self._apost_hook(
            "async_moderation_hook",
            {
                "data": data,
                "user_api_key_dict": user_api_key_dict,
                "call_type": call_type,
            },
        )

    @instrument_callback
    async def async_post_call_streaming_hook(self, user_api_key_dict: Any, response: Any):
        """Unused hook for aggregate streaming responses (handled via WebSocket)."""
        # Aggregate streaming responses are handled by the WebSocket channel.
        return None

    async def _close_async_iterator(
        self,
        iterator: AsyncGenerator[Any, None] | AsyncIterator[Any],
    ) -> None:
        """Attempt best-effort ``aclose`` on *iterator* without raising errors."""
        aclose = getattr(iterator, "aclose", None)
        if callable(aclose):
            close_callable = cast(Callable[[], Awaitable[None]], aclose)
            with contextlib.suppress(Exception):
                await close_callable()

    @instrument_callback
    async def async_post_call_streaming_iterator_hook(
        self,
        user_api_key_dict: Any,
        response: AsyncGenerator[Any, None],
        request_data: dict[str, Any],
    ) -> AsyncGenerator[ModelResponseStream, None]:
        """Proxy streaming responses while normalising Anthropic payloads."""
        stream_id = request_data.get("litellm_call_id")
        if not stream_id:
            verbose_logger.warning("stream request missing litellm_call_id; dropping stream")
            await self._close_async_iterator(response)
            return

        sanitized_request = self._json_safe(request_data)

        connection: StreamConnection | None = None
        try:
            connection = await StreamConnection.create(
                stream_id=stream_id,
                control_plane_url=self.control_plane_url,
            )
            await connection.send({"type": "START", "data": sanitized_request})
        except Exception as exc:  # pragma: no cover - network failure path
            verbose_logger.error(
                "stream[%s] unable to establish control plane connection: %s",
                stream_id,
                exc,
            )
            if connection is not None:
                with contextlib.suppress(Exception):
                    await connection.close()
            await self._close_async_iterator(response)
            return

        upstream = response
        model_name = request_data.get("model")
        if _is_anthropic_model(model_name):
            upstream = self._normalize_anthropic_stream(response)

        chunk_logger = get_callback_chunk_logger()
        orchestrator = StreamOrchestrator(
            stream_id=stream_id,
            connection=connection,
            upstream=upstream,
            normalize_chunk=self._normalize_stream_chunk,
            timeout=self.stream_timeout,
            chunk_logger=chunk_logger,
        )

        try:
            async for transformed in orchestrator.run():
                yield transformed
        except StreamTimeoutError as exc:
            verbose_logger.error("stream[%s] control plane timeout: %s", stream_id, exc)
        except StreamOrchestrationError as exc:
            verbose_logger.error("stream[%s] orchestration failed: %s", stream_id, exc)
        except Exception as exc:  # pragma: no cover - defensive
            verbose_logger.error("stream[%s] unexpected streaming failure: %s", stream_id, exc)
        finally:
            try:
                await connection.close()
            except Exception as exc:  # pragma: no cover - defensive
                verbose_logger.error("stream[%s] connection close failed: %s", stream_id, exc)

    async def _normalize_anthropic_stream(
        self,
        upstream: AsyncGenerator[Any, None] | AsyncIterator[Any],
    ) -> AsyncGenerator[ModelResponseStream, None]:
        """Yield OpenAI-style chunks from an Anthropic SSE stream."""
        adapter = AnthropicToOpenAIAdapter()
        try:
            async for raw_chunk in upstream:
                payload_bytes = self._coerce_sse_bytes(raw_chunk)
                for chunk_dict in adapter.process(payload_bytes):
                    yield ModelResponseStream.model_validate(chunk_dict)
        finally:
            for chunk_dict in adapter.finalize():
                yield ModelResponseStream.model_validate(chunk_dict)

    @staticmethod
    def _coerce_sse_bytes(raw_chunk: Any) -> bytes:
        """Best-effort conversion of SSE payloads to bytes."""
        if isinstance(raw_chunk, bytes):
            return raw_chunk
        if isinstance(raw_chunk, str):
            return raw_chunk.encode("utf-8")
        raise TypeError(f"Unsupported Anthropic stream payload type: {type(raw_chunk).__name__}")

    def _normalize_stream_chunk(self, chunk: dict) -> ModelResponseStream:
        if not isinstance(chunk, dict):
            raise TypeError(f"policy stream chunks must be dict, got {type(chunk).__name__}")

        payload = dict(chunk)
        payload.pop("_source_type_", None)

        required_keys = {"choices", "model", "created"}
        missing = sorted(required_keys - payload.keys())
        if missing:
            raise ValueError(f"policy stream chunk missing required fields: {missing}")

        return ModelResponseStream.model_validate(payload)

    def _json_safe(self, obj: Any) -> Any:
        try:
            import json as _json

            _json.dumps(obj)
            return obj
        except Exception:
            pass

        if hasattr(obj, "model_dump"):
            try:
                return self._json_safe(obj.model_dump())
            except Exception:
                pass
        if hasattr(obj, "dict"):
            try:
                return self._json_safe(obj.dict())
            except Exception:
                pass

        if isinstance(obj, dict):
            return {self._json_safe(k): self._json_safe(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [self._json_safe(v) for v in obj]
        if isinstance(obj, (str, int, float, bool)) or obj is None:
            return obj
        return repr(obj)


unified_callback = UnifiedCallback()
