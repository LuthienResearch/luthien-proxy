# ABOUTME: Custom logger implementation for LiteLLM that hooks into all LLM calls for AI control
# ABOUTME: Implements async pre/post/streaming hooks that communicate with the control plane service

import json
import os
from typing import Any, AsyncGenerator, Dict, Literal, Optional, Union
import uuid

import httpx
from beartype import beartype
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy.proxy_server import DualCache, UserAPIKeyAuth
from litellm.types.utils import ModelResponse, ModelResponseStream


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
        """
        Pre-call hook that:
        1. Adds episode/step IDs for correlation
        2. Fetches policy from control plane
        3. Optionally modifies requests (tool gating, system prompt hardening)
        4. Can reject requests by returning a string response
        """
        try:
            # Add correlation IDs for tracking
            episode_id = str(uuid.uuid4())
            step_id = str(uuid.uuid4())

            # Add tracking metadata to request
            data["luthien_metadata"] = {
                "episode_id": episode_id,
                "step_id": step_id,
                "user_id": user_api_key_dict.user_id if user_api_key_dict else None,
                "call_type": call_type,
            }

            # Call control plane for pre-call evaluation
            payload = {
                "stage": "pre",
                "episode_id": episode_id,
                "step_id": step_id,
                "call_type": call_type,
                "request": data,
                "user_metadata": {
                    "user_id": user_api_key_dict.user_id if user_api_key_dict else None,
                    "team_id": getattr(user_api_key_dict, "team_id", None)
                    if user_api_key_dict
                    else None,
                },
            }

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.control_plane_url}/policy/evaluate", json=payload
                )
                response.raise_for_status()
                decision = response.json()

            # Handle control plane decisions
            if decision.get("reject"):
                # Return string to short-circuit with rejection message
                return decision.get(
                    "reject_message", "Request rejected by AI control policy"
                )

            if decision.get("rewrite"):
                # Modify the request based on policy
                data.update(decision["rewrite"])

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
        response: ModelResponse,
    ) -> Optional[ModelResponse]:
        """
        Post-call hook that:
        1. Scores response suspiciousness
        2. Can trigger trusted editing/deferral
        3. Can trigger resampling protocol
        4. Logs decisions for audit
        """
        try:
            # Extract metadata from request
            metadata = data.get("luthien_metadata", {})

            payload = {
                "stage": "post",
                "episode_id": metadata.get("episode_id"),
                "step_id": metadata.get("step_id"),
                "call_type": metadata.get("call_type"),
                "request": data,
                "response": response.model_dump()
                if hasattr(response, "model_dump")
                else dict(response),
                "user_metadata": metadata.get("user_metadata", {}),
            }

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response_eval = await client.post(
                    f"{self.control_plane_url}/policy/evaluate", json=payload
                )
                response_eval.raise_for_status()
                action = response_eval.json()

            # Handle control plane actions
            if action.get("replace_response"):
                # Replace response with trusted edit
                return ModelResponse(**action["replace_response"])

            if action.get("trigger_resample"):
                # Trigger resampling protocol
                resample_response = await self._handle_resample(data, response, action)
                if resample_response:
                    return resample_response

            # Return original response if no intervention needed
            return response

        except Exception as e:
            print(f"Error in post-call hook: {e}")
            return response

    @beartype
    async def async_post_call_streaming_iterator_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        response: AsyncGenerator[ModelResponseStream, None],
        request_data: Dict[str, Any],
    ) -> AsyncGenerator[ModelResponseStream, None]:
        """
        Streaming hook that:
        1. Buffers and periodically evaluates chunks
        2. Can halt stream and switch to trusted model
        3. Implements streaming cutover for safety
        """
        try:
            buffer = []
            metadata = request_data.get("luthien_metadata", {})
            chunk_count = 0

            async for chunk in response:
                chunk_count += 1

                # Extract text content from chunk
                chunk_text = ""
                if hasattr(chunk, "choices") and chunk.choices:
                    delta = getattr(chunk.choices[0], "delta", None)
                    if delta and hasattr(delta, "content") and delta.content:
                        chunk_text = delta.content

                buffer.append(chunk_text)

                # Optimistically yield chunk while evaluating
                yield chunk

                # Periodic evaluation every ~50 chunks or 400 characters
                accumulated_text = "".join(buffer)
                if chunk_count % 50 == 0 or len(accumulated_text) > 400:
                    should_halt = await self._evaluate_streaming_chunk(
                        request_data, accumulated_text, chunk_text, metadata
                    )

                    if should_halt:
                        # Halt original stream and switch to trusted model
                        async for trusted_chunk in self._trusted_stream_replacement(
                            request_data, accumulated_text, metadata
                        ):
                            yield trusted_chunk
                        break

        except Exception as e:
            print(f"Error in streaming hook: {e}")
            # Continue yielding original stream on error
            async for chunk in response:
                yield chunk

    @beartype
    async def _evaluate_streaming_chunk(
        self,
        request_data: Dict[str, Any],
        accumulated_text: str,
        latest_chunk: str,
        metadata: Dict[str, Any],
    ) -> bool:
        """Evaluate if streaming should be halted based on current content."""
        try:
            payload = {
                "stage": "streaming_chunk",
                "episode_id": metadata.get("episode_id"),
                "step_id": metadata.get("step_id"),
                "accumulated_text": accumulated_text,
                "latest_chunk": latest_chunk,
                "request": request_data,
            }

            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    f"{self.control_plane_url}/policy/on_chunk", json=payload
                )
                response.raise_for_status()
                decision = response.json()

            return decision.get("halt_stream", False)

        except Exception as e:
            print(f"Error evaluating streaming chunk: {e}")
            return False

    @beartype
    async def _trusted_stream_replacement(
        self,
        request_data: Dict[str, Any],
        partial_content: str,
        metadata: Dict[str, Any],
    ) -> AsyncGenerator[ModelResponseStream, None]:
        """Generate trusted replacement stream when original is halted."""
        try:
            payload = {
                "episode_id": metadata.get("episode_id"),
                "step_id": metadata.get("step_id"),
                "original_request": request_data,
                "partial_content": partial_content,
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                async with client.stream(
                    "POST",
                    f"{self.control_plane_url}/trusted/stream_replacement",
                    json=payload,
                ) as response:
                    response.raise_for_status()

                    async for line in response.aiter_lines():
                        if line.strip():
                            try:
                                chunk_data = json.loads(line)
                                yield ModelResponseStream(**chunk_data)
                            except json.JSONDecodeError:
                                continue

        except Exception as e:
            print(f"Error generating trusted replacement stream: {e}")
            # Yield a simple completion message on error
            yield ModelResponseStream(
                choices=[
                    {
                        "delta": {
                            "content": "[Response modified by AI control system]"
                        },
                        "index": 0,
                        "finish_reason": None,
                    }
                ]
            )

    @beartype
    async def _handle_resample(
        self,
        request_data: Dict[str, Any],
        original_response: ModelResponse,
        action: Dict[str, Any],
    ) -> Optional[ModelResponse]:
        """Handle defer-to-resample protocol."""
        try:
            metadata = request_data.get("luthien_metadata", {})

            payload = {
                "episode_id": metadata.get("episode_id"),
                "step_id": metadata.get("step_id"),
                "original_request": request_data,
                "original_response": original_response.model_dump()
                if hasattr(original_response, "model_dump")
                else dict(original_response),
                "resample_config": action.get("resample_config", {}),
            }

            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.control_plane_url}/resample", json=payload
                )
                response.raise_for_status()
                result = response.json()

            if result.get("replacement_response"):
                return ModelResponse(**result["replacement_response"])

            return None

        except Exception as e:
            print(f"Error handling resample: {e}")
            return None


# Global instance for LiteLLM to use
luthien_logger = LuthienControlLogger()
