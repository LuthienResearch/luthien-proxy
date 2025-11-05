# ABOUTME: PolicyExecutorProtocol interface - handles block assembly and policy hook invocation
# ABOUTME: Implementations can customize timeout strategies

"""Policy executor protocol for streaming and request processing."""

import asyncio
from typing import AsyncIterator, Protocol

from litellm.types.utils import ModelResponse

from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.observability.context import ObservabilityContext
from luthien_proxy.v2.streaming.protocol import PolicyContext


class PolicyExecutorProtocol(Protocol):
    """Executes policy logic for both request and streaming response processing.

    Implementations handle:
    - Request processing through policy hooks
    - Block assembly from incoming ModelResponse stream
    - Streaming response policy hook invocation at key moments
    """

    async def process_request(
        self,
        request: Request,
        policy_ctx: PolicyContext,
        obs_ctx: ObservabilityContext,
    ) -> Request:
        """Execute policy processing on request before backend invocation.

        Args:
            request: Incoming request from client
            policy_ctx: Policy context for shared state
            obs_ctx: Observability context for tracing

        Returns:
            Policy-modified request to send to backend

        Raises:
            Exception: On policy errors
        """
        ...

    async def process(
        self,
        input_stream: AsyncIterator[ModelResponse],
        output_queue: asyncio.Queue[ModelResponse],
        policy_ctx: PolicyContext,
        obs_ctx: ObservabilityContext,
    ) -> None:
        """Execute policy processing on streaming chunks.

        This method:
        1. Reads ModelResponse chunks from input_stream
        2. Feeds them to block assembly to build partial/complete blocks
        3. Invokes policy hooks at appropriate moments
        4. Writes policy-approved ModelResponse chunks to output_queue
        5. Monitors for timeout based on implementation strategy

        Args:
            input_stream: Stream of ModelResponse chunks from backend
            output_queue: Queue to write policy-approved ModelResponse chunks to
            policy_ctx: Policy context for shared state
            obs_ctx: Observability context for tracing

        Raises:
            Exception: On policy errors or assembly failures
        """
        ...


class PolicyTimeoutError(Exception):
    """Raised when policy processing exceeds configured timeout."""

    pass


__all__ = ["PolicyExecutorProtocol", "PolicyTimeoutError"]
