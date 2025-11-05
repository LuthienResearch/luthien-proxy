# ABOUTME: PolicyExecutor interface - handles block assembly and policy hook invocation
# ABOUTME: Implementations can customize timeout strategies

"""Policy executor interface for streaming responses."""

import asyncio
from typing import Any, Protocol

from luthien_proxy.v2.observability.context import ObservabilityContext
from luthien_proxy.v2.streaming.protocol import PolicyContext


class PolicyExecutor(Protocol):
    """Executes policy logic during streaming response processing.

    Implementations handle:
    - Block assembly from incoming chunks
    - Policy hook invocation at key moments
    - Timeout monitoring (implementation-specific)
    - Keepalive signaling from policies

    All processing happens in common chunk format.
    """

    def keepalive(self) -> None:
        """Signal that policy is actively working.

        Policies call this during long-running operations (e.g., waiting
        for trusted monitor response) to prevent timeout. Implementation
        determines how this affects timeout monitoring.
        """
        ...

    async def process(
        self,
        input_queue: asyncio.Queue[Any],  # Common format chunks (ingress)
        output_queue: asyncio.Queue[Any],  # Common format chunks (egress)
        policy_ctx: PolicyContext,
        obs_ctx: ObservabilityContext,
    ) -> None:
        """Execute policy processing on streaming chunks.

        This method:
        1. Reads common-format chunks from input_queue
        2. Feeds them to block assembly to build partial/complete blocks
        3. Invokes policy hooks at appropriate moments
        4. Writes policy-approved chunks to output_queue
        5. Monitors for timeout based on implementation strategy

        Args:
            input_queue: Queue of common-format chunks from backend
            output_queue: Queue for policy-approved common-format chunks
            policy_ctx: Policy context for shared state
            obs_ctx: Observability context for tracing

        Raises:
            PolicyTimeoutError: If processing exceeds timeout
            Exception: On policy errors or assembly failures
        """
        ...


class PolicyTimeoutError(Exception):
    """Raised when policy processing exceeds configured timeout."""

    pass


__all__ = ["PolicyExecutor", "PolicyTimeoutError"]
