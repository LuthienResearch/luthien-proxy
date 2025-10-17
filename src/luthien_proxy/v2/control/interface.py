# ABOUTME: Protocol definition for control plane service - network-ready interface
# ABOUTME: Can be implemented locally or remotely without changing gateway code

"""Control plane service interface.

This defines the protocol for interacting with the control plane,
whether it's in-process (ControlPlaneLocal) or networked (ControlPlaneHTTP).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator, Protocol

if TYPE_CHECKING:
    from luthien_proxy.v2.control.models import PolicyEvent
    from luthien_proxy.v2.messages import FullResponse, Request, StreamingResponse


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
    - Policies process explicit message types (Request, FullResponse, StreamingResponse)
    - Policies emit PolicyEvents describing their activity
    - Control plane collects events for logging/UI
    """

    async def process_request(
        self,
        request: Request,
        call_id: str,
    ) -> Request:
        """Apply policies to incoming request before LLM call.

        Args:
            request: The request to process
            call_id: Unique identifier for this request/response cycle

        Returns:
            Transformed request

        Raises:
            Exception: If policy rejects the request
        """
        ...

    async def process_full_response(
        self,
        response: FullResponse,
        call_id: str,
    ) -> FullResponse:
        """Apply policies to complete response after LLM call.

        Args:
            response: The full response to process
            call_id: Unique identifier for this request/response cycle

        Returns:
            Potentially transformed response

        Raises:
            Exception: If policy rejects the response
        """
        ...

    async def process_streaming_response(
        self,
        incoming: AsyncIterator[StreamingResponse],
        call_id: str,
    ) -> AsyncIterator[StreamingResponse]:
        """Apply policies to streaming responses with reactive processing.

        The control plane bridges the policy's queue-based reactive interface
        with the gateway's async iterator interface. Policies run as reactive
        tasks that process incoming chunks and emit outgoing chunks independently.

        No 1:1 mapping required - policies can buffer, filter, split, inject,
        rewrite, or synthesize chunks based on accumulated context.

        Args:
            incoming: Async iterator of chunks from LLM
            call_id: Unique identifier for this request/response cycle

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
