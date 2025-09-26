# ABOUTME: Minimal callback skeleton that LiteLLM can load directly
# ABOUTME: Acts as a thin proxy forwarding all calls to the control plane

"""Minimal LiteLLM callback that forwards all calls to the control plane.

This module is loaded by LiteLLM via the `callbacks` entry in
`config/litellm_config.yaml`.

Implements a thin bridge:
- All hooks are forwarded via `POST /api/hooks/{hook_name}` (fire-and-forget)
- Streaming iterator events are wrapped to forward each chunk through the same path
"""

import os
import time as _time
from typing import AsyncGenerator, Optional, Union

import httpx
import pydantic
from litellm._logging import verbose_logger
from litellm.integrations.custom_logger import CustomLogger
from litellm.types.utils import ModelResponseStream


class LuthienCallback(CustomLogger):
    """Thin callback that forwards everything to the control plane."""

    def __init__(self):
        """Initialize callback with control-plane endpoint and defaults."""
        super().__init__()
        self.control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8081")
        self.timeout = 10.0
        verbose_logger.info(f"LuthienCallback initialized with control plane URL: {self.control_plane_url}")

    # ------------- internal helpers -------------
    async def _apost_hook(
        self,
        hook: str,
        payload: dict,
    ) -> Optional[dict]:
        """Send an async hook payload to the control plane (fire-and-forget).

        Returns:
            Parsed JSON from the control plane when available, otherwise None.

        Raises:
            httpx.HTTPError: If returned status is 4xx/5xx or response is invalid.
            httpx.TimeoutException: If the request times out.
            httpx.ConnectError: If the connection to the control plane fails.


        Note:
            Connection, timeout, and HTTP status errors are logged and suppressed to
            avoid breaking the proxy, but malformed success responses raise an
            `httpx.HTTPError` so we fail fast on unexpected control-plane behavior.
        """
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
                try:
                    return response.json()
                except ValueError:
                    raise httpx.HTTPError(f"Invalid JSON response from control plane for {hook} hook")
        except httpx.ConnectError as e:
            # Network connectivity issues - likely transient
            verbose_logger.error(f"Network error (potentially transient) posting {hook} hook: {e}")
            return None
        except httpx.HTTPStatusError as e:
            # HTTP error response - could be persistent configuration issue
            if e.response.status_code >= 500:
                verbose_logger.error(f"Control plane server error for {hook} hook: {e}")
            else:
                verbose_logger.error(f"Client error posting {hook} hook (possible misconfiguration): {e}")
            return None
        except httpx.TimeoutException as e:
            # Timeout - likely transient but could indicate overload
            verbose_logger.error(f"Timeout posting {hook} hook: {e}")
            return None

    # --------------------- Hooks ----------------------

    async def async_pre_call_hook(self, **kwargs) -> Optional[Union[Exception, str, dict]]:
        """Forward pre-call data; may return a string/Exception to short-circuit."""
        await self._apost_hook(
            "async_pre_call_hook",
            self._json_safe(kwargs),
        )

    async def async_post_call_failure_hook(self, **kwargs):
        """Notify control plane of a failed call."""
        await self._apost_hook(
            "async_post_call_failure_hook",
            self._json_safe(kwargs),
        )
        return None

    async def async_post_call_success_hook(self, **kwargs):
        """Allow control plane to replace final response for non-streaming calls."""
        result = await self._apost_hook(
            "async_post_call_success_hook",
            self._json_safe(kwargs),
        )
        return result

    async def async_moderation_hook(self, **kwargs):
        """Forward moderation evaluations to the control plane."""
        await self._apost_hook(
            "async_moderation_hook",
            self._json_safe(kwargs),
        )
        return None

    async def async_post_call_streaming_hook(self, **kwargs):
        """Forward aggregate streaming info post-call."""
        await self._apost_hook(
            "async_post_call_streaming_hook",
            self._json_safe(kwargs),
        )
        return None

    @staticmethod
    def _update_cumulative_choices(
        cumulative_choices: list[list[dict]], cumulative_tokens: list[list[str]], new_tokens: list[str], response: dict
    ) -> None:
        """Update cumulative choices and tokens from a new response chunk."""
        if "choices" in response and isinstance(response["choices"], list):
            choices = response["choices"]
            for choice in choices:
                if "index" not in choice:
                    raise ValueError(f"_update_cumulative_choices: choice missing index! {choice}")
                index: int = choice["index"]
                # Ensure lists are long enough
                while len(cumulative_tokens) <= index:
                    cumulative_tokens.append([])
                while len(cumulative_choices) <= index:
                    cumulative_choices.append([])
                while len(new_tokens) <= index:
                    new_tokens.append("")
                cumulative_choices[index].append(choice)
                cumulative_tokens[index].append(choice.get("delta", {}).get("content", ""))
                new_tokens[index] = cumulative_tokens[index][-1]  # last token for this choice
        return

    async def async_post_call_streaming_iterator_hook(
        self, user_api_key_dict, response, request_data: dict
    ) -> AsyncGenerator[ModelResponseStream, None]:
        """Wrap the streaming iterator to allow per-chunk edits or suppression."""
        try:
            cumulative_choices: list[list[dict]] = []
            cumulative_tokens: list[list[str]] = []
            new_tokens: list[str] = []
            item: ModelResponseStream
            async for item in response:
                serialized_chunk: dict = item.model_dump()
                LuthienCallback._update_cumulative_choices(
                    cumulative_choices, cumulative_tokens, new_tokens, serialized_chunk
                )
                policy_result = await self._apost_hook(
                    "async_post_call_streaming_iterator_hook",
                    {
                        "cumulative_tokens": cumulative_tokens,
                        "new_tokens": new_tokens,
                        "response": serialized_chunk,
                        "request_data": self._json_safe(request_data),
                        "cumulative_choices": cumulative_choices,
                        "user_api_key_dict": self._json_safe(user_api_key_dict),
                    },
                )
                if policy_result is None:
                    # No edit, yield original chunk
                    verbose_logger.error("async_post_call_streaming_iterator_hook: no policy_result, yielding original")
                    # TODO: fallback behavior should be configurable (e.g. suppress vs yield original)
                    yield item
                else:
                    yield self._normalize_stream_chunk(policy_result)
        except Exception as e:
            verbose_logger.error(f"async_post_call_streaming_iterator_hook error: {e}")
            # If wrapping fails, yield from original response to avoid breaking stream
            async for item in response:
                yield item
        finally:
            return

    def _normalize_stream_chunk(self, chunk: dict) -> ModelResponseStream:
        """Normalize policy-provided stream chunks back to ModelResponseStream."""
        if chunk is None:
            raise ValueError("policy returned no stream chunk")

        if not isinstance(chunk, dict):
            raise TypeError(f"policy stream chunks must be dict, got {type(chunk).__name__}")

        payload = dict(chunk)
        payload.pop("_source_type_", None)

        if not payload:
            raise ValueError("policy returned empty stream chunk")

        required_keys = {"choices", "model", "created"}
        missing_keys = sorted(required_keys - payload.keys())
        if missing_keys:
            raise ValueError(f"policy stream chunk missing required fields: {missing_keys}")

        try:
            return ModelResponseStream.model_validate(payload)
        except pydantic.ValidationError as exc:
            raise ValueError(
                f"Policy returned invalid data, unable to build ModelResponseStream from {payload}"
            ) from exc

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
            verbose_logger.warning(f"Failed to repr() object of type {type(obj).__name__}, using placeholder")
            return "<unserializable>"


# Create the singleton instance that LiteLLM will use
luthien_callback = LuthienCallback()
