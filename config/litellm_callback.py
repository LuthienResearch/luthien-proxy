# ABOUTME: Minimal callback skeleton that LiteLLM can load directly
# ABOUTME: Acts as a thin proxy forwarding all calls to the control plane

"""Minimal LiteLLM callback that forwards all calls to the control plane.

This module is loaded by LiteLLM via the `callbacks` entry in
`config/litellm_config.yaml`.

Implements a thin bridge:
- All hooks are forwarded via `POST /hooks/{hook_name}` (fire-and-forget)
- Streaming iterator events are wrapped to forward each chunk through the same path
"""

import os
import time as _time
from typing import Any, AsyncGenerator, Optional, Union

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
    ) -> Any:
        """Send an async hook payload to the control plane (fire-and-forget).

        Returns:
            The JSON response from control plane if successful, None otherwise.

        Note:
            Errors are logged but not raised to prevent proxy failures.
            Distinguishes between transient (network) and persistent (config) errors.
        """
        try:
            payload["post_time_ns"] = _time.time_ns()
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.control_plane_url}/hooks/{hook}",
                    json=self._json_safe(payload),
                )
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if "application/json" in content_type.lower() and response.content:
                    try:
                        return response.json()
                    except ValueError:
                        return None
                return None
        except httpx.ConnectError as e:
            # Network connectivity issues - likely transient
            verbose_logger.warning(f"Transient network error posting {hook} hook: {e}")
            return None
        except httpx.HTTPStatusError as e:
            # HTTP error response - could be persistent configuration issue
            if e.response.status_code >= 500:
                verbose_logger.warning(f"Control plane server error for {hook} hook: {e}")
            else:
                verbose_logger.error(f"Client error posting {hook} hook (possible misconfiguration): {e}")
            return None
        except httpx.TimeoutException as e:
            # Timeout - likely transient but could indicate overload
            verbose_logger.warning(f"Timeout posting {hook} hook: {e}")
            return None
        except Exception as e:
            # Unexpected error - likely persistent
            verbose_logger.error(f"Unexpected error posting {hook} hook: {e}")
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
    ) -> AsyncGenerator[Any, None]:
        """Wrap the streaming iterator to allow per-chunk edits or suppression."""
        try:
            cumulative_choices: list[list[dict]] = []
            cumulative_tokens: list[list[str]] = []
            new_tokens: list[str] = []
            async for item in response:
                serialized_chunk = self._serialize_response(item)
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
                yield self._normalize_stream_chunk(item, policy_result)
        except Exception as e:
            verbose_logger.error(f"async_post_call_streaming_iterator_hook error: {e}")
            # If wrapping fails, yield from original response to avoid breaking stream
            async for item in response:
                yield item
        finally:
            return

    # Fallback for LiteLLM versions that emit async_on_stream_event instead of iterator hook
    async def async_on_stream_event(self, kwargs, response_obj, start_time, end_time) -> None:
        """Compatibility wrapper for LiteLLM variants emitting per-chunk events."""
        try:
            request_data = None
            user_api_key_dict = None
            try:
                if isinstance(kwargs, dict):
                    request_data = kwargs.get("request_data") or kwargs.get("data") or kwargs.get("kwargs")
                    user_api_key_dict = kwargs.get("user_api_key_dict")
            except Exception:
                request_data = None
                user_api_key_dict = None

            await self._apost_hook(
                "async_post_call_streaming_iterator_hook",
                {
                    "user_api_key_dict": self._json_safe(user_api_key_dict),
                    "response": self._serialize_response(response_obj),
                    "request_data": (self._json_safe(request_data) if request_data else {}),
                },
            )
        except Exception as e:
            verbose_logger.error(f"async_on_stream_event forward error: {e}")
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
            raise ValueError("response is None")
        if isinstance(response, dict):
            serialized = dict(response)
            serialized["_source_type_"] = "dict"
            return serialized
        elif isinstance(response, pydantic.BaseModel):
            response_dict = response.model_dump()
            response_dict["_source_type_"] = "pydantic"
            return response_dict
        else:
            raise ValueError("response is not a dict or pydantic model!")

    def _normalize_stream_chunk(self, original: Any, edited: Any | None) -> ModelResponseStream:
        """Normalize stream chunks from the policy back to ModelResponseStream.

        Args:
            original: The original chunk from upstream.
            edited: The potentially edited chunk from the policy.

        Returns:
            A valid ModelResponseStream object.

        Raises:
            TypeError: If the chunk cannot be normalized to ModelResponseStream.
            ValueError: If required fields are missing.
        """
        if edited is None:
            # No edits from policy, return original if valid
            if isinstance(original, ModelResponseStream):
                return original
            raise TypeError(f"expected ModelResponseStream, got {type(original).__name__}")

        # Policy provided edits
        if isinstance(edited, ModelResponseStream):
            return edited

        if isinstance(edited, dict):
            try:
                payload = dict(edited)
                payload.pop("_source_type_", None)

                # Validate required fields exist before attempting conversion
                if not payload:
                    raise ValueError("Empty payload dictionary from policy")

                # Check for partial dict or missing required structure
                # These are essential fields for a valid stream response
                if "choices" not in payload or "model" not in payload or "created" not in payload:
                    verbose_logger.warning(
                        f"Policy returned incomplete stream chunk: missing required fields. Keys: {list(payload.keys())}"
                    )
                    # Fall back to original for incomplete responses
                    if isinstance(original, ModelResponseStream):
                        return original
                    raise ValueError("Incomplete stream chunk and no valid original to fall back to")

                return ModelResponseStream.model_validate(payload)
            except (pydantic.ValidationError, ValueError) as e:
                verbose_logger.error(f"Failed to validate stream chunk from policy: {e}")
                # Fall back to original if policy response is invalid
                if isinstance(original, ModelResponseStream):
                    verbose_logger.warning("Falling back to original stream chunk due to policy error")
                    return original
                raise

        raise TypeError(f"unexpected policy stream result type: {type(edited).__name__}")

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
