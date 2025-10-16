# ABOUTME: Base class for V2 policies - reactive streaming with explicit message types
# ABOUTME: Policies are tasks that build responses by reacting to incoming information

"""Policy handler base class for V2 architecture.

Extend PolicyHandler to implement custom policies for your proxy.

Policies process three message types:
1. Request - transform/validate requests before sending to LLM
2. FullResponse - transform/validate complete responses
3. StreamingResponse - reactive task that builds output based on incoming chunks

Key insight for streaming:
Policies are NOT simple filters or transforms. They are **reactive tasks** that:
- Run continuously as information arrives from the LLM
- Maintain state and context across all chunks seen so far
- Make decisions about what to output based on full available context
- Can call other services (including other LLMs) to inform decisions
- Have full control over output timing and content

This enables complex behaviors like:
- LLM-based content judgment (call a judge LLM with accumulated context)
- Response rewriting (buffer input, rewrite with another LLM, stream result)
- Adaptive routing (switch to different LLM mid-stream based on quality)
- Multi-source synthesis (combine multiple LLM outputs intelligently)

Policies also emit PolicyEvents to describe their activity (for logging, UI, debugging).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional

from luthien_proxy.v2.control.models import PolicyEvent
from luthien_proxy.v2.messages import FullResponse, Request, StreamingResponse
from luthien_proxy.v2.streaming import ChunkQueue

if TYPE_CHECKING:
    from typing import Any, Callable

    PolicyEventHandler = Callable[[PolicyEvent], None]


class PolicyHandler(ABC):
    """Base class for policy handlers.

    Policies have two responsibilities:
    1. **Message processing**: Transform/validate requests and responses
    2. **Event emission**: Emit PolicyEvents describing their activity

    Override these methods to implement custom policies:
    - process_request: Transform/validate requests before sending to LLM
    - process_full_response: Transform/validate complete responses
    - process_streaming_response: Reactive task that builds output stream

    The streaming method is the most powerful - it's a long-running task that
    reacts to incoming chunks and decides what to output. It can maintain state,
    call other services, and make complex decisions based on accumulated context.
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
    async def process_request(self, request: Request) -> Request:
        """Process a request before sending to LLM.

        Transform, validate, or enrich the request. Emit events to describe
        what you did and why.

        Args:
            request: Request to process

        Returns:
            Transformed request

        Raises:
            Exception: If request should be rejected (will be caught by control plane)
        """
        pass

    @abstractmethod
    async def process_full_response(self, response: FullResponse) -> FullResponse:
        """Process a complete (non-streaming) response.

        Transform or validate the response. Emit events to describe
        what you did and why.

        Args:
            response: Full response to process

        Returns:
            Transformed response

        Raises:
            Exception: If response should be rejected (will be caught by control plane)
        """
        pass

    @abstractmethod
    async def process_streaming_response(
        self,
        incoming: ChunkQueue[StreamingResponse],
        outgoing: ChunkQueue[StreamingResponse],
    ) -> None:
        """Reactive streaming task: build output response based on incoming chunks.

        This is NOT a simple filter/transform pipeline. This is a **task** that runs
        continuously, reacting to new information as it arrives from the LLM and
        deciding what to send to the client.

        The policy builds the output stream by:
        - Reading incoming chunks as they arrive (via incoming.get_available())
        - Using all available context (accumulated state, current chunks, etc.)
        - Deciding what chunks to emit (via outgoing.put())
        - Potentially calling other services/LLMs to make decisions
        - Maintaining state across iterations

        Examples of what policies might do:
        - **Simple passthrough**: Forward chunks unchanged
        - **Content filtering**: Check each chunk, abort if forbidden content detected
        - **Buffering/merging**: Accumulate N chunks, emit merged version
        - **LLM-based judgment**: Call a judge LLM with context so far, decide whether to continue
        - **Response rewriting**: Use a separate LLM to rephrase the response
        - **Multi-source synthesis**: Combine chunks from multiple LLMs
        - **Adaptive routing**: Switch to different LLM mid-stream based on content

        Pattern:
            state = PolicyState()  # Maintain context

            while True:
                # Get all chunks that just arrived (blocks until at least one available)
                new_chunks = await incoming.get_available()
                if not new_chunks:  # Stream ended
                    break

                # Update state with new information
                state.update(new_chunks)

                # Make decisions based on full context
                decisions = await self.decide_next_actions(state)

                # Emit responses
                for chunk in decisions.chunks_to_emit:
                    await outgoing.put(chunk)

                # Check if we should stop
                if decisions.should_abort:
                    break

        Args:
            incoming: Queue to read chunks from LLM (may be empty, may have many)
            outgoing: Queue to write chunks for client

        Raises:
            Exception: If processing fails critically
        """
        pass


class DefaultPolicyHandler(PolicyHandler):
    """Default policy implementation with common examples.

    Demonstrates:
    - Token limit enforcement with event emission
    - Content filtering with stream abortion and event emission
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

    async def process_request(self, request: Request) -> Request:
        """Process request with token limit enforcement."""
        # Policy: Enforce token limits
        if request.max_tokens and request.max_tokens > self.max_tokens:
            original = request.max_tokens
            request.max_tokens = self.max_tokens

            self.emit_event(
                event_type="token_limit_enforced",
                summary=f"Clamped max_tokens from {original} to {self.max_tokens}",
                details={"original": original, "clamped": self.max_tokens},
                severity="info",
            )

            if self.verbose:
                print(f"[POLICY] Clamped max_tokens from {original} to {self.max_tokens}")

        return request

    async def process_full_response(self, response: FullResponse) -> FullResponse:
        """Process full response (pass through in this example)."""
        if self.verbose:
            # Access the underlying ModelResponse
            model_response = response.to_model_response()
            content = model_response.choices[0].message.content
            tokens_used = model_response.usage.total_tokens
            print(f"[POLICY] Response length: {len(content)} chars, {tokens_used} tokens")

        return response

    async def process_streaming_response(
        self,
        incoming: ChunkQueue[StreamingResponse],
        outgoing: ChunkQueue[StreamingResponse],
    ) -> None:
        """Process streaming with content filtering."""
        try:
            while True:
                # Get all currently available chunks
                batch = await incoming.get_available()
                if not batch:  # Stream ended
                    break

                # Process each chunk in the batch
                for streaming_response in batch:
                    # Extract content from chunk
                    chunk = streaming_response.to_model_response()
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

                        # Create a canned response chunk
                        canned_chunk = chunk.model_copy(deep=True)
                        canned_chunk.choices[0].delta.content = "[Content filtered by policy]"

                        # Emit the canned message and stop
                        await outgoing.put(StreamingResponse.from_model_response(canned_chunk))
                        return  # Abort the stream

                    # Default: pass through unchanged
                    await outgoing.put(streaming_response)
        finally:
            # Always close outgoing queue when done
            await outgoing.close()


__all__ = [
    "PolicyHandler",
    "DefaultPolicyHandler",
]
