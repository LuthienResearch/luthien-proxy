# ABOUTME: Base class for V2 policies - simplified interface for streaming and non-streaming
# ABOUTME: User-facing abstraction that developers extend to implement custom policies

"""Policy handler base class for V2 architecture.

Extend PolicyHandler to implement custom policies for your proxy.
All policy methods receive requests/responses in OpenAI format (litellm.ModelResponse).
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from luthien_proxy.v2.control.models import StreamAction

if TYPE_CHECKING:
    from typing import Any

    ModelResponse = Any  # LiteLLM's ModelResponse has incomplete type annotations


class StreamControl:
    """Controls the behavior of streaming policies.

    Policies can modify this object to signal actions like abort or model switch.
    """

    def __init__(self):
        """Initialize stream control with default values."""
        self.should_abort = False
        self.replacement_stream = None
        self.metadata: dict = {}


class PolicyHandler(ABC):
    """Base class for policy handlers.

    Override these methods to implement custom policies:
    - apply_request_policies: Modify/validate requests before sending to LLM
    - apply_response_policy: Modify/validate complete responses
    - apply_streaming_chunk_policy: Control streaming behavior chunk-by-chunk
    """

    @abstractmethod
    async def apply_request_policies(self, data: dict) -> dict:
        """Apply policies to requests before sending to LLM.

        Args:
            data: Request data in OpenAI format (model, messages, etc.)

        Returns:
            Modified request data

        Raises:
            Exception: If request should be rejected
        """
        pass

    @abstractmethod
    async def apply_response_policy(self, response: ModelResponse) -> ModelResponse:
        """Apply policies to complete (non-streaming) responses.

        Args:
            response: litellm.ModelResponse object in OpenAI format

        Returns:
            Modified response (or same response)
        """
        pass

    @abstractmethod
    async def apply_streaming_chunk_policy(
        self,
        chunk: ModelResponse,
        outgoing_queue: asyncio.Queue,
        control: StreamControl,
    ) -> StreamAction:
        """Apply policies to streaming chunks.

        This function has full control over the stream and can:
        - Add chunks to outgoing_queue (zero, one, or many per incoming chunk)
        - Modify chunks before adding them
        - Return ABORT to stop the upstream stream
        - Return SWITCH_MODEL to switch to a different model
        - Inject canned responses
        - Buffer chunks and send them in batches

        Args:
            chunk: Incoming chunk from upstream (litellm.ModelResponse)
            outgoing_queue: Queue to put outgoing chunks for the client
            control: Control object to signal stream behavior

        Returns:
            StreamAction indicating what to do with the upstream
        """
        pass


class DefaultPolicyHandler(PolicyHandler):
    """Default policy implementation with common examples.

    Customize by subclassing or modifying these methods.
    """

    def __init__(self, max_tokens: int = 4096, verbose: bool = True):
        """Initialize default policy handler.

        Args:
            max_tokens: Maximum tokens to allow per request
            verbose: Whether to print policy decisions
        """
        self.max_tokens = max_tokens
        self.verbose = verbose
        self.forbidden_words = ["FORBIDDEN_WORD", "CENSORED"]  # Example list

    async def apply_request_policies(self, data: dict) -> dict:
        """Apply pre-request policies."""
        if self.verbose:
            print(f"[POLICY] Request to model: {data.get('model')}")
            print(f"[POLICY] Message count: {len(data.get('messages', []))}")

        # Policy: Enforce token limits
        if data.get("max_tokens", 0) > self.max_tokens:
            if self.verbose:
                print(f"[POLICY] Clamping max_tokens from {data['max_tokens']} to {self.max_tokens}")
            data["max_tokens"] = self.max_tokens

        # Policy: Add metadata for tracking
        if "metadata" not in data:
            data["metadata"] = {}
        data["metadata"]["proxy_version"] = "2.0.0"

        # Policy: Block certain models (example)
        # if "gpt-4o-mini" in data.get("model", ""):
        #     raise Exception("gpt-4o-mini is not allowed by policy")

        # Policy: Enforce minimum temperature (example)
        # if data.get("temperature", 1.0) < 0.3:
        #     data["temperature"] = 0.3

        return data

    async def apply_response_policy(self, response: ModelResponse) -> ModelResponse:
        """Apply post-response policies for non-streaming."""
        if self.verbose:
            content = response.choices[0].message.content
            tokens_used = response.usage.total_tokens
            print(f"[POLICY] Response length: {len(content)} chars, {tokens_used} tokens")

        # Policy: Log response metadata
        # Could log to database, metrics system, etc.

        # Policy: Content filtering (example)
        # content = response.choices[0].message.content
        # if any(word in content for word in self.forbidden_words):
        #     response.choices[0].message.content = "[Content filtered]"

        return response

    async def apply_streaming_chunk_policy(
        self,
        chunk: ModelResponse,
        outgoing_queue: asyncio.Queue,
        control: StreamControl,
    ) -> StreamAction:
        """Apply policies to streaming chunks."""
        content = chunk.choices[0].delta.content or ""

        # Policy: Content filtering with stream abortion
        if any(word in content for word in self.forbidden_words):
            if self.verbose:
                print("[POLICY] Forbidden content detected, aborting stream")

            # Send a canned response
            canned_chunk = chunk.model_copy(deep=True)
            canned_chunk.choices[0].delta.content = "[Content filtered by policy]"
            await outgoing_queue.put(canned_chunk)

            # Abort the upstream stream
            control.should_abort = True
            return StreamAction.ABORT

        # Policy: Rate limiting (example)
        # await asyncio.sleep(0.01)  # Artificial delay

        # Policy: Transform content (example)
        # if content:
        #     chunk.choices[0].delta.content = content.upper()

        # Default: pass through the chunk unchanged
        await outgoing_queue.put(chunk)
        return StreamAction.CONTINUE


__all__ = [
    "PolicyHandler",
    "StreamControl",
    "DefaultPolicyHandler",
]
