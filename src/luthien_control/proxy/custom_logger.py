# ABOUTME: Custom logger implementation for LiteLLM that hooks into all LLM calls for AI control
# ABOUTME: Implements async pre/post/streaming hooks that communicate with the control plane service

import json
import os
from typing import Any, AsyncGenerator, Dict, Literal, Optional, Union

import httpx
from beartype import beartype
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy.proxy_server import DualCache, UserAPIKeyAuth


class LuthienControlLogger(CustomLogger):
    """Custom logger that implements Redwood-style AI control via LiteLLM hooks."""

    def __init__(self):
        super().__init__()
        self.control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://localhost:8081")
        self.timeout = 10.0  # seconds

    @beartype
    async def async_pre_call_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        cache: DualCache,
        data: Dict[str, Any],
        call_type: Literal[
            "completion",
            "text_completion",
            "embeddings",
            "image_generation",
            "moderation",
            "audio_transcription",
        ],
    ) -> Optional[Union[Dict[str, Any], str]]:
        """Thin wrapper: forward to control plane hook endpoint."""
        try:
            payload = {
                "user_api_key_dict": _serialize_user_key(user_api_key_dict),
                "cache": None,
                "data": data,
                "call_type": call_type,
            }
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(
                    f"{self.control_plane_url}/hooks/pre", json=payload
                )
                r.raise_for_status()
                res = r.json()
            rt = res.get("result_type")
            if rt == "string":
                return res.get("string")
            if rt == "dict":
                return res.get("dict")
            return data

        except Exception as e:
            # Fail-safe: allow request to proceed but log the error
            print(f"Error in pre-call hook: {e}")
            return data

    @beartype
    async def async_post_call_success_hook(
        self,
        data: Dict[str, Any],
        user_api_key_dict: UserAPIKeyAuth,
        response: Any,
    ) -> Optional[Dict[str, Any]]:
        """Thin wrapper: forward to control plane hook endpoint."""
        try:
            payload = {
                "data": data,
                "user_api_key_dict": _serialize_user_key(user_api_key_dict),
                "response": response.model_dump()
                if hasattr(response, "model_dump")
                else response,
            }
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(
                    f"{self.control_plane_url}/hooks/post_success", json=payload
                )
                r.raise_for_status()
                res = r.json()
            if res.get("replace"):
                return res.get("replacement")
            return None

        except Exception as e:
            print(f"Error in post-call hook: {e}")
            return response

    @beartype
    async def async_post_call_streaming_iterator_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        response: AsyncGenerator[Any, None],
        request_data: Dict[str, Any],
    ) -> AsyncGenerator[Any, None]:
        """Chunk-by-chunk control: consult control plane per chunk; can pass/suppress/edit or replace stream entirely."""
        try:
            accumulated_text = ""
            chunk_index = 0

            async for chunk in response:
                chunk_index += 1
                # Convert chunk to a plain dict for transport
                chunk_dict = (
                    chunk.model_dump()
                    if hasattr(chunk, "model_dump")
                    else (chunk if isinstance(chunk, dict) else {"raw": str(chunk)})
                )
                # Extract delta text for accumulation
                accumulated_text += _extract_chunk_text(chunk, chunk_dict)

                payload = {
                    "user_api_key_dict": _serialize_user_key(user_api_key_dict),
                    "request_data": request_data,
                    "chunk": chunk_dict,
                    "chunk_index": chunk_index,
                    "accumulated_text": accumulated_text,
                }

                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    r = await client.post(
                        f"{self.control_plane_url}/hooks/stream_chunk", json=payload
                    )
                    r.raise_for_status()
                    decision = r.json()

                action = decision.get("action", "pass")

                if action == "pass":
                    yield chunk
                elif action == "suppress":
                    continue
                elif action == "edit":
                    edited = decision.get("chunk")
                    if edited is not None:
                        yield edited
                    else:
                        yield chunk
                elif action == "replace_stream":
                    # Switch to policy-provided replacement stream
                    async with httpx.AsyncClient(timeout=None) as client:
                        async with client.stream(
                            "POST",
                            f"{self.control_plane_url}/hooks/stream_replacement",
                            json={"request_data": request_data},
                        ) as s:
                            async for line in s.aiter_lines():
                                if not line:
                                    continue
                                try:
                                    chunk_data = json.loads(line)
                                    yield chunk_data
                                except json.JSONDecodeError:
                                    continue
                    break
                else:
                    # Unknown action; pass through
                    yield chunk

        except Exception as e:
            print(f"Error in streaming hook: {e}")
            # Continue yielding original stream on error
            async for chunk in response:
                yield chunk


def _extract_chunk_text(chunk: Any, chunk_dict: Dict[str, Any]) -> str:
    # Try to extract delta.content from OpenAI-style streams
    try:
        if hasattr(chunk, "choices") and chunk.choices:
            delta = getattr(chunk.choices[0], "delta", None)
            if delta and hasattr(delta, "content") and delta.content:
                return delta.content
        if "choices" in chunk_dict and chunk_dict["choices"]:
            delta = chunk_dict["choices"][0].get("delta", {})
            if isinstance(delta, dict) and "content" in delta:
                return delta.get("content") or ""
    except Exception:
        pass
    return ""


def _serialize_user_key(user_key: Optional[UserAPIKeyAuth]) -> Optional[Dict[str, Any]]:
    if not user_key:
        return None
    out: Dict[str, Any] = {}
    for attr in ("user_id", "team_id", "email", "org_id"):
        val = getattr(user_key, attr, None)
        if val is not None:
            out[attr] = val
    return out


# Global instance for LiteLLM to use
luthien_logger = LuthienControlLogger()
