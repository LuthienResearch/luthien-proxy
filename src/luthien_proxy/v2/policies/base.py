# ABOUTME: Base class for V2 policies - simplified interface with event emission
# ABOUTME: Policies decide what to forward and emit PolicyEvents describing their activity

"""Policy handler base class for V2 architecture.

Extend PolicyHandler to implement custom policies for your proxy.
Policies:
1. Decide what content to forward (transform/filter requests and responses)
2. Emit PolicyEvents to describe their activity (for logging, UI, debugging)
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional

from luthien_proxy.v2.control.models import PolicyEvent, StreamAction

if TYPE_CHECKING:
    from typing import Any, Callable

    ModelResponse = Any  # LiteLLM's ModelResponse has incomplete type annotations
    PolicyEventHandler = Callable[[PolicyEvent], None]


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

    Policies have two responsibilities:
    1. **Content control**: Decide what to forward (transform, filter, validate)
    2. **Event emission**: Emit PolicyEvents describing their activity

    Override these methods to implement custom policies:
    - apply_request_policies: Transform/validate requests before sending to LLM
    - apply_response_policy: Transform/validate complete responses
    - apply_streaming_chunk_policy: Control streaming behavior chunk-by-chunk
    """

    def __init__(self):
        """Initialize policy handler."""
        self._event_handler: Optional[PolicyEventHandler] = None
        self._call_id: Optional[str] = None

    def set_event_handler(self, handler: PolicyEventHandler) -> None:
        """Set the event handler for emitting policy events.

        The control plane calls this to provide a callback for event emission.
        """
        self._event_handler = handler

    def set_call_id(self, call_id: str) -> None:
        """Set the current call ID for event emission."""
        self._call_id = call_id

    def emit_event(
        self,
        event_type: str,
        summary: str,
        details: Optional[dict[str, Any]] = None,
        severity: str = "info",
    ) -> None:
        """Emit a policy event.

        Args:
            event_type: Type of event (e.g., 'request_modified', 'content_filtered')
            summary: Human-readable summary of what happened
            details: Additional structured data about the event
            severity: Severity level (debug, info, warning, error)
        """
        if self._event_handler and self._call_id:
            event = PolicyEvent(
                event_type=event_type,
                call_id=self._call_id,
                summary=summary,
                details=details or {},
                severity=severity,
            )
            self._event_handler(event)

    @abstractmethod
    async def apply_request_policies(self, data: dict) -> dict:
        """Apply policies to requests before sending to LLM.

        Transform, validate, or enrich the request. Emit events to describe
        what you did and why.

        Args:
            data: Request data in OpenAI format (model, messages, etc.)

        Returns:
            Modified request data

        Raises:
            Exception: If request should be rejected (will be caught by control plane)
        """
        pass

    @abstractmethod
    async def apply_response_policy(self, response: ModelResponse) -> ModelResponse:
        """Apply policies to complete (non-streaming) responses.

        Transform or validate the response. Emit events to describe
        what you did and why.

        Args:
            response: litellm.ModelResponse object in OpenAI format

        Returns:
            Modified response (or same response)

        Raises:
            Exception: If response should be rejected (will be caught by control plane)
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
        - Emit events to describe decisions

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

    Demonstrates:
    - Token limit enforcement with event emission
    - Metadata injection
    - Content filtering with event emission
    """

    def __init__(self, max_tokens: int = 4096, verbose: bool = True):
        """Initialize default policy handler.

        Args:
            max_tokens: Maximum tokens to allow per request
            verbose: Whether to print policy decisions
        """
        super().__init__()
        self.max_tokens = max_tokens
        self.verbose = verbose
        self.forbidden_words = ["FORBIDDEN_WORD", "CENSORED"]  # Example list

    async def apply_request_policies(self, data: dict) -> dict:
        """Apply pre-request policies."""
        # Policy: Enforce token limits
        if data.get("max_tokens", 0) > self.max_tokens:
            original = data["max_tokens"]
            data["max_tokens"] = self.max_tokens

            self.emit_event(
                event_type="token_limit_enforced",
                summary=f"Clamped max_tokens from {original} to {self.max_tokens}",
                details={"original": original, "clamped": self.max_tokens},
                severity="info",
            )

            if self.verbose:
                print(f"[POLICY] Clamped max_tokens from {original} to {self.max_tokens}")

        # Policy: Add metadata for tracking
        if "metadata" not in data:
            data["metadata"] = {}
        data["metadata"]["proxy_version"] = "2.0.0"

        return data

    async def apply_response_policy(self, response: ModelResponse) -> ModelResponse:
        """Apply post-response policies for non-streaming."""
        if self.verbose:
            content = response.choices[0].message.content
            tokens_used = response.usage.total_tokens
            print(f"[POLICY] Response length: {len(content)} chars, {tokens_used} tokens")

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
            self.emit_event(
                event_type="content_filtered",
                summary="Forbidden content detected, aborting stream",
                details={"matched_words": [w for w in self.forbidden_words if w in content]},
                severity="warning",
            )

            if self.verbose:
                print("[POLICY] Forbidden content detected, aborting stream")

            # Send a canned response
            canned_chunk = chunk.model_copy(deep=True)
            canned_chunk.choices[0].delta.content = "[Content filtered by policy]"
            await outgoing_queue.put(canned_chunk)

            # Abort the upstream stream
            control.should_abort = True
            return StreamAction.ABORT

        # Default: pass through the chunk unchanged
        await outgoing_queue.put(chunk)
        return StreamAction.CONTINUE


__all__ = [
    "PolicyHandler",
    "StreamControl",
    "DefaultPolicyHandler",
]
