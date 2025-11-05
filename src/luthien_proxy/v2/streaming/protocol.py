# ABOUTME: Protocol definitions for streaming pipeline components
# ABOUTME: Defines StreamProcessor protocol and PolicyContext for cross-stage state management

"""Protocol definitions for the streaming pipeline architecture.

This module defines the core abstractions for the streaming pipeline:
- StreamProcessor: Protocol for pipeline stages that process queued data
- PolicyContext: Shared mutable state across request/response lifecycle
"""

import asyncio
import time
from typing import Any, Protocol, TypeVar

T_in = TypeVar("T_in")
T_out = TypeVar("T_out")


class StreamProcessor(Protocol[T_in, T_out]):
    """Protocol for a stage in the streaming pipeline.

    Each processor consumes items from an input queue, processes them,
    and produces items to an output queue. Processors receive context
    objects for observability and policy state management.

    Type parameters:
        T_in: Type of items consumed from input_queue
        T_out: Type of items produced to output_queue
    """

    async def process(
        self,
        input_queue: asyncio.Queue[T_in],
        output_queue: asyncio.Queue[T_out],
        policy_ctx: "PolicyContext",
        obs_ctx: "ObservabilityContext",  # type: ignore[name-defined]  # noqa: F821
    ) -> None:
        """Process items from input queue to output queue.

        This method should:
        1. Read items from input_queue
        2. Process them (transform, validate, apply policy, etc.)
        3. Write results to output_queue
        4. Use policy_ctx for cross-stage state
        5. Use obs_ctx for observability (spans, events, metrics)

        The processor should run until input_queue is closed/exhausted or
        an error occurs. Errors should be propagated by raising exceptions.

        Args:
            input_queue: Queue to consume items from
            output_queue: Queue to produce items to
            policy_ctx: Policy context for state management and keepalive
            obs_ctx: Observability context for tracing/metrics/events

        Raises:
            Exception: Any processing errors encountered
        """
        ...


class PolicyContext:
    """Shared mutable state across the entire request/response lifecycle.

    This context is created at the gateway level and passed through both
    request processing and streaming response processing. It provides:

    1. Cross-stage state: Policies can store and retrieve data that persists
       across multiple policy invocations and pipeline stages
    2. Keepalive mechanism: Long-running policies can signal they're still
       actively working to prevent timeout

    The context is NOT thread-safe and should only be accessed from async
    code within a single request handler.
    """

    def __init__(self, transaction_id: str) -> None:
        """Initialize policy context for a request.

        Args:
            transaction_id: Unique identifier for this request/response cycle
        """
        self.transaction_id = transaction_id
        self._scratchpad: dict[str, Any] = {}
        self._last_keepalive = time.monotonic()

    @property
    def scratchpad(self) -> dict[str, Any]:
        """Mutable dictionary for storing arbitrary policy state.

        Policies can use this to share state across invocations. For example:
        - Track whether a safety check has been performed
        - Store intermediate results from trusted monitors
        - Accumulate metrics across streaming chunks

        Returns:
            Mutable dictionary unique to this context
        """
        return self._scratchpad

    async def keepalive(self) -> None:
        """Signal that policy is actively working, resetting timeout.

        Long-running policies (e.g., waiting for trusted monitor response)
        should call this periodically to indicate they haven't stalled.

        This resets the "last activity" timestamp used by timeout monitoring.
        """
        self._last_keepalive = time.monotonic()

    def time_since_keepalive(self) -> float:
        """Time in seconds since last keepalive (or context creation).

        Used by timeout monitors to determine if policy has exceeded limits.

        Returns:
            Seconds since last keepalive() call or __init__
        """
        return time.monotonic() - self._last_keepalive


__all__ = ["StreamProcessor", "PolicyContext"]
