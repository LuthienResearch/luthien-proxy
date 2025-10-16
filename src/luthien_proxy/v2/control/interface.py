# ABOUTME: Protocol definition for control plane service - network-ready interface
# ABOUTME: Can be implemented locally or remotely without changing gateway code

"""Control plane service interface.

This defines the protocol for interacting with the control plane,
whether it's in-process (ControlPlaneLocal) or networked (ControlPlaneHTTP).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator, Protocol

if TYPE_CHECKING:
    from typing import Any

    from luthien_proxy.types import JSONObject
    from luthien_proxy.v2.control.models import PolicyResult, RequestMetadata, StreamingContext

    ModelResponse = Any  # LiteLLM's ModelResponse has incomplete type annotations


class ActivityEvent(Protocol):
    """Protocol for activity events published to UI."""

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        ...


class ControlPlaneService(Protocol):
    """Interface for control plane operations.

    This protocol defines the contract between the API gateway and the control logic.
    It can be implemented as:
    - In-process calls (ControlPlaneLocal)
    - HTTP client (ControlPlaneHTTP)
    - gRPC client (ControlPlaneGRPC)
    - Any other RPC mechanism

    The gateway code doesn't need to know which implementation is being used.
    """

    async def apply_request_policies(
        self,
        request_data: dict,
        metadata: RequestMetadata,
    ) -> PolicyResult[dict]:
        """Apply policies to incoming request before LLM call.

        Args:
            request_data: The request payload (OpenAI format)
            metadata: Request metadata (call_id, user_id, etc.)

        Returns:
            PolicyResult with transformed request data and allowed/denied status

        Raises:
            PolicyError: If policy execution fails critically
        """
        ...

    async def apply_response_policy(
        self,
        response: ModelResponse,
        metadata: RequestMetadata,
    ) -> PolicyResult[ModelResponse]:
        """Apply policies to complete response after LLM call.

        Args:
            response: The ModelResponse from LiteLLM
            metadata: Request metadata

        Returns:
            PolicyResult with potentially transformed response

        Raises:
            PolicyError: If policy execution fails critically
        """
        ...

    async def create_streaming_context(
        self,
        request_data: dict,
        metadata: RequestMetadata,
    ) -> StreamingContext:
        """Initialize streaming context and return stream ID.

        Args:
            request_data: The request payload (OpenAI format)
            metadata: Request metadata

        Returns:
            StreamingContext with stream_id and initial state

        Raises:
            PolicyError: If policy initialization fails
        """
        ...

    async def process_streaming_chunk(
        self,
        chunk: ModelResponse,
        context: StreamingContext,
    ) -> AsyncIterator[PolicyResult[ModelResponse]]:
        """Process a streaming chunk through policies.

        This is an async generator because policies may:
        - Emit zero chunks (buffer/filter)
        - Emit one chunk (passthrough/transform)
        - Emit many chunks (split/augment)

        Args:
            chunk: The incoming chunk from LiteLLM
            context: The streaming context (mutable, updated by policy)

        Yields:
            PolicyResult for each outgoing chunk

        Raises:
            PolicyError: If policy execution fails critically
        """
        ...

    async def publish_activity(
        self,
        event: ActivityEvent,
    ) -> None:
        """Publish activity event for UI consumption.

        Args:
            event: Activity event to publish

        Note:
            This should not raise exceptions - activity publishing is best-effort
        """
        ...

    async def log_debug_event(
        self,
        debug_type: str,
        payload: JSONObject,
    ) -> None:
        """Log debug event to database.

        Args:
            debug_type: Type of debug event (e.g., "request_policy", "response_policy")
            payload: Event payload

        Note:
            This should not raise exceptions - debug logging is best-effort
        """
        ...


__all__ = ["ControlPlaneService", "ActivityEvent"]
