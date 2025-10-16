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

    from luthien_proxy.v2.control.models import PolicyEvent, RequestMetadata, StreamingContext

    ModelResponse = Any  # LiteLLM's ModelResponse has incomplete type annotations


class ControlPlaneService(Protocol):
    """Interface for control plane operations.

    This protocol defines the contract between the API gateway and the control logic.
    It can be implemented as:
    - In-process calls (ControlPlaneLocal)
    - HTTP client (ControlPlaneHTTP)
    - gRPC client (ControlPlaneGRPC)
    - Any other RPC mechanism

    The gateway code doesn't need to know which implementation is being used.

    Simplified interface:
    - Policies decide what content to forward
    - Policies emit PolicyEvents describing their activity
    - Control plane collects events for logging/UI
    """

    async def apply_request_policies(
        self,
        request_data: dict,
        metadata: RequestMetadata,
    ) -> dict:
        """Apply policies to incoming request before LLM call.

        Args:
            request_data: The request payload (OpenAI format)
            metadata: Request metadata (call_id, user_id, etc.)

        Returns:
            Transformed request data

        Raises:
            Exception: If policy rejects the request
        """
        ...

    async def apply_response_policy(
        self,
        response: ModelResponse,
        metadata: RequestMetadata,
    ) -> ModelResponse:
        """Apply policies to complete response after LLM call.

        Args:
            response: The ModelResponse from LiteLLM
            metadata: Request metadata

        Returns:
            Potentially transformed response

        Raises:
            Exception: If policy rejects the response
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
            Exception: If policy initialization fails
        """
        ...

    async def process_streaming_chunk(
        self,
        chunk: ModelResponse,
        context: StreamingContext,
    ) -> AsyncIterator[ModelResponse]:
        """Process a streaming chunk through policies.

        This is an async generator because policies may:
        - Emit zero chunks (buffer/filter)
        - Emit one chunk (passthrough/transform)
        - Emit many chunks (split/augment)

        Args:
            chunk: The incoming chunk from LiteLLM
            context: The streaming context (mutable, updated by policy)

        Yields:
            Outgoing chunks for the client

        Raises:
            Exception: If policy execution fails critically
        """
        ...

    async def get_events(self, call_id: str) -> list[PolicyEvent]:
        """Get all events for a specific call.

        Args:
            call_id: The call ID to get events for

        Returns:
            List of PolicyEvents for this call
        """
        ...


__all__ = ["ControlPlaneService"]
