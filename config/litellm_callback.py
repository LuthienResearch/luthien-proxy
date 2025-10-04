# ABOUTME: Minimal callback skeleton that LiteLLM can load directly
# ABOUTME: Acts as a thin proxy forwarding all calls to the control plane

"""Minimal LiteLLM callback that forwards all calls to the control plane."""

import asyncio
import contextlib
import os
import time as _time
from typing import Any, AsyncGenerator, Mapping, Optional, Union

import httpx
import pydantic
from litellm._logging import verbose_logger
from litellm.integrations.custom_logger import CustomLogger
from litellm.types.utils import ModelResponseStream

from luthien_proxy.proxy.callback_chunk_logger import get_callback_chunk_logger
from luthien_proxy.proxy.stream_connection_manager import StreamConnectionManager


class LuthienCallback(CustomLogger):
    """Thin callback that forwards everything to the control plane."""

    def __init__(self):
        """Initialize callback with control-plane endpoint and defaults."""
        super().__init__()
        self.control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://control-plane:8081")
        self.timeout = 10.0
        verbose_logger.info(f"LuthienCallback initialized with control plane URL: {self.control_plane_url}")
        self._connection_manager: Optional[StreamConnectionManager] = None

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
                    verbose_logger.error(f"Unexpected content-type from control plane for {hook} hook: {content_type}")
                    raise httpx.HTTPError(f"Unexpected content-type from control plane for {hook} hook: {content_type}")
                if not response.content:
                    verbose_logger.warning(f"Empty response from control plane for {hook} hook")
                    raise httpx.HTTPError(f"Empty response from control plane for {hook} hook")
                try:
                    parsed = response.json()
                    verbose_logger.debug(f"Control plane response for {hook}: {str(parsed)[:300]}")
                    return parsed
                except ValueError as e:
                    verbose_logger.error(
                        f"Invalid JSON response from control plane for {hook} hook: {e}, content={response.content[:200]}"
                    )
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

    def _apply_policy_response(self, response: Any, policy_result: Any) -> None:
        """Mutate *response* using the structure returned by the policy."""
        if response is None or policy_result is None:
            return

        # Convert BaseModel responses from the control plane into plain dicts.
        if isinstance(policy_result, pydantic.BaseModel):
            payload: Mapping[str, Any] = policy_result.model_dump()
        elif isinstance(policy_result, Mapping):
            payload = policy_result
        else:
            raise TypeError(f"Policy result must be Mapping or BaseModel, got {type(policy_result).__name__}")

        if isinstance(response, dict):
            response.clear()
            response.update(payload)
            return

        if isinstance(response, pydantic.BaseModel):
            # Drop helper markers that the policy might include for debugging.
            prepared: dict[str, Any] = dict(payload)
            prepared.pop("_source_type_", None)

            try:
                updated = response.__class__(**prepared)
            except Exception as exc:  # pragma: no cover - defensive
                raise ValueError(
                    f"Policy returned data that failed validation for {response.__class__.__name__}: {exc}"
                ) from exc

            response.__dict__.clear()
            response.__dict__.update(updated.__dict__)

            if hasattr(updated, "__pydantic_extra__"):
                object.__setattr__(
                    response,
                    "__pydantic_extra__",
                    getattr(updated, "__pydantic_extra__", None),
                )
            if hasattr(updated, "__pydantic_fields_set__"):
                object.__setattr__(
                    response,
                    "__pydantic_fields_set__",
                    getattr(updated, "__pydantic_fields_set__", set()),
                )
            return

        raise TypeError(f"Unsupported response type: {type(response).__name__}")

    # --------------------- Hooks ----------------------

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: Any,
        data: dict,
        call_type: str,
    ) -> Optional[Union[Exception, str, dict]]:
        """Forward pre-call data; may return a string/Exception to short-circuit."""
        await self._apost_hook(
            "async_pre_call_hook",
            {
                "user_api_key_dict": user_api_key_dict,
                "cache": cache,
                "data": data,
                "call_type": call_type,
            },
        )

    async def async_post_call_failure_hook(
        self,
        request_data: dict,
        original_exception: Exception,
        user_api_key_dict: Any,
        traceback_str: Optional[str] = None,
    ):
        """Notify control plane of a failed call."""
        await self._apost_hook(
            "async_post_call_failure_hook",
            {
                "request_data": request_data,
                "original_exception": original_exception,
                "user_api_key_dict": user_api_key_dict,
                "traceback_str": traceback_str,
            },
        )
        return None

    async def async_post_call_success_hook(self, data: dict, user_api_key_dict: Any, response: Any):
        """Allow control plane to replace final response for non-streaming calls."""
        result = await self._apost_hook(
            "async_post_call_success_hook",
            {
                "data": data,
                "user_api_key_dict": user_api_key_dict,
                "response": response,
            },
        )
        verbose_logger.debug(
            f"Control plane hook result: type={type(result)} is_none={result is None} preview={str(result)[:300]}"
        )

        if result is None:
            return result

        try:
            self._apply_policy_response(response, result)
        except Exception as exc:  # pragma: no cover - defensive
            verbose_logger.error(f"Failed to apply control plane response override: {exc}")

        return result

    async def async_moderation_hook(self, data: dict, user_api_key_dict: Any, call_type: str):
        """Forward moderation evaluations to the control plane."""
        await self._apost_hook(
            "async_moderation_hook",
            {
                "data": data,
                "user_api_key_dict": user_api_key_dict,
                "call_type": call_type,
            },
        )
        return None

    async def async_post_call_streaming_hook(self, user_api_key_dict: Any, response: Any):
        """Skip forwarding aggregate streaming info (handled via WebSocket)."""
        return None

    def _get_connection_manager(self) -> StreamConnectionManager:
        if self._connection_manager is None:
            self._connection_manager = StreamConnectionManager(self.control_plane_url)
        return self._connection_manager

    async def _cleanup_stream(self, stream_id: str, send_end: bool) -> None:
        manager = self._get_connection_manager()
        connection = await manager.lookup(stream_id)
        if connection is None:
            return

        if send_end:
            try:
                await connection.send({"type": "END"})
            except Exception as exc:
                verbose_logger.error(f"stream[{stream_id}] failed to notify END: {exc}")

        await manager.close(stream_id)

    async def async_post_call_streaming_iterator_hook(
        self,
        user_api_key_dict: Any,
        response: AsyncGenerator[ModelResponseStream, None],
        request_data: dict[str, Any],
    ) -> AsyncGenerator[ModelResponseStream, None]:
        """Forward streaming chunks through the control plane WebSocket."""
        stream_id = request_data.get("litellm_call_id")
        if not stream_id:
            async for item in response:
                yield item
            return

        sanitized_request = self._json_safe(request_data)

        try:
            manager = self._get_connection_manager()
            connection = await manager.get_or_create(stream_id, sanitized_request)
        except Exception as exc:
            verbose_logger.error(f"stream[{stream_id}] unable to establish control plane connection: {exc}")
            async for item in response:
                yield item
            return

        passthrough = False
        cleanup_after_stream = False
        stream_closed = False

        receive_task: asyncio.Task[Optional[dict]] | None = None
        control_signaled_end = False

        def schedule_receive() -> None:
            """Kick off the control-plane receive task when needed."""
            nonlocal receive_task
            if receive_task is None and not stream_closed and not passthrough:
                verbose_logger.debug(f"stream[{stream_id}] scheduling control receive task")
                receive_task = asyncio.create_task(connection.receive())

        chunk_logger = get_callback_chunk_logger()
        control_chunk_index = [0]  # Use list for mutability in nested function
        client_chunk_index = [0]

        async def poll_control(initial_timeout: float | None) -> list[ModelResponseStream]:
            """Drain any buffered control-plane output within the timeout window."""
            nonlocal receive_task, passthrough, cleanup_after_stream, stream_closed, control_signaled_end

            chunks: list[ModelResponseStream] = []
            timeout = initial_timeout
            needs_reschedule = False

            while receive_task is not None:
                task = receive_task
                if timeout is not None:
                    done, _ = await asyncio.wait({task}, timeout=timeout)
                    if not done:
                        break
                    timeout = None
                else:
                    if not task.done():
                        break

                receive_task = None
                try:
                    message = task.result()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # pragma: no cover - defensive
                    verbose_logger.error(f"stream[{stream_id}] control receive failed: {exc}")
                    passthrough = True
                    cleanup_after_stream = True
                    break

                if message is None:
                    stream_closed = True
                    break

                chunk_logger.log_control_chunk_received(stream_id, message, control_chunk_index[0])
                control_chunk_index[0] += 1

                msg_type = message.get("type")
                if msg_type == "CHUNK":
                    data = message.get("data")
                    try:
                        normalized = self._normalize_stream_chunk(data)
                        chunk_logger.log_chunk_normalized(stream_id, data, success=True)
                        chunks.append(normalized)
                    except Exception as exc:
                        verbose_logger.error(f"stream[{stream_id}] invalid transformed chunk: {exc}")
                        chunk_logger.log_chunk_normalized(stream_id, data, success=False, error=str(exc))
                    needs_reschedule = True
                elif msg_type == "ERROR":
                    verbose_logger.error(f"stream[{stream_id}] control plane error: {message.get('error')}")
                    passthrough = True
                    cleanup_after_stream = True
                    break
                elif msg_type == "END":
                    verbose_logger.debug(f"stream[{stream_id}] control plane signaled END")
                    stream_closed = True
                    control_signaled_end = True
                    break
                else:
                    verbose_logger.warning(
                        f"stream[{stream_id}] unexpected message type {msg_type}; ignoring control output"
                    )
                    needs_reschedule = True

                timeout = None

            if needs_reschedule and not stream_closed and not passthrough:
                schedule_receive()

            return chunks

        try:
            schedule_receive()
            async for item in response:
                if stream_closed:
                    verbose_logger.debug(f"stream[{stream_id}] upstream chunk skipped; control closed")
                for pending in await poll_control(initial_timeout=0):
                    pending_dict = pending.model_dump()
                    chunk_logger.log_chunk_to_client(stream_id, pending_dict, client_chunk_index[0])
                    client_chunk_index[0] += 1
                    yield pending
                if stream_closed or passthrough:
                    break

                chunk_dict = item.model_dump()
                try:
                    await connection.send({"type": "CHUNK", "data": chunk_dict})
                except Exception as exc:
                    verbose_logger.error(f"stream[{stream_id}] unable to forward chunk: {exc}")
                    passthrough = True
                    cleanup_after_stream = True
                    continue

                transformed_chunks = await poll_control(initial_timeout=0.05)
                for transformed in transformed_chunks:
                    transformed_dict = transformed.model_dump()
                    chunk_logger.log_chunk_to_client(stream_id, transformed_dict, client_chunk_index[0])
                    client_chunk_index[0] += 1
                    yield transformed

                if stream_closed or passthrough:
                    break

            # Upstream finished; flush remaining control-plane output.
            for transformed in await poll_control(initial_timeout=0.05):
                transformed_dict = transformed.model_dump()
                chunk_logger.log_chunk_to_client(stream_id, transformed_dict, client_chunk_index[0])
                client_chunk_index[0] += 1
                yield transformed
            while True:
                extra = await poll_control(initial_timeout=None)
                if not extra:
                    break
                for transformed in extra:
                    transformed_dict = transformed.model_dump()
                    chunk_logger.log_chunk_to_client(stream_id, transformed_dict, client_chunk_index[0])
                    client_chunk_index[0] += 1
                    yield transformed
        finally:
            if receive_task is not None:
                receive_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await receive_task
            try:
                await self._cleanup_stream(
                    stream_id,
                    send_end=not control_signaled_end,
                )
            except Exception as exc:  # pragma: no cover - defensive
                verbose_logger.error(f"stream[{stream_id}] cleanup failed: {exc}")

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        """Hook called when stream completes successfully."""
        stream_id = kwargs.get("litellm_params", {}).get("metadata", {}).get("litellm_call_id")
        if not stream_id:
            return
        try:
            await self._cleanup_stream(stream_id, send_end=True)
        except Exception as exc:
            verbose_logger.error(f"stream[{stream_id}] success cleanup failed: {exc}")

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        """Hook called when a stream terminates in failure."""
        stream_id = kwargs.get("litellm_params", {}).get("metadata", {}).get("litellm_call_id")
        if not stream_id:
            return
        try:
            await self._cleanup_stream(stream_id, send_end=False)
        except Exception as exc:
            verbose_logger.error(f"stream[{stream_id}] failure cleanup failed: {exc}")

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
        - Pydantic models: converted to dict via model_dump() or dict()
        - Other objects: converted to string via repr()
        """
        try:
            import json as _json

            _json.dumps(obj)  # Fast path: already serializable
            return obj
        except Exception:
            pass

        # Try Pydantic model serialization first
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
