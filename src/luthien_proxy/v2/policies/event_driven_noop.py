# ABOUTME: NoOp pass-through policy using EventDrivenPolicy DSL
# ABOUTME: Demonstrates simplest possible event-driven policy implementation

"""NoOp pass-through policy using EventDrivenPolicy.

This policy demonstrates the simplest possible EventDrivenPolicy implementation:
it forwards all chunks unchanged by overriding only on_chunk_complete.

Example:
    policy:
      class: "luthien_proxy.v2.policies.event_driven_noop:EventDrivenNoOpPolicy"
      config: {}
"""

from __future__ import annotations

import logging
from typing import Any

from litellm.types.utils import ModelResponse

from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.policies.base import LuthienPolicy
from luthien_proxy.v2.policies.context import PolicyContext
from luthien_proxy.v2.streaming import EventDrivenPolicy, StreamingContext

logger = logging.getLogger(__name__)


class EventDrivenNoOpPolicy(EventDrivenPolicy, LuthienPolicy):
    """NoOp policy using EventDrivenPolicy DSL.

    This policy demonstrates the minimal implementation:
    - No state needed (create_state returns None)
    - Only on_chunk_complete overridden to forward chunks
    - All other hooks use default no-op implementations

    This is the event-driven equivalent of the default LuthienPolicy
    streaming behavior, but demonstrates the hook-based approach.
    """

    def __init__(self):
        """Initialize policy."""
        logger.info("EventDrivenNoOpPolicy initialized")

    def create_state(self) -> Any:
        """No state needed for pass-through."""
        return None

    async def on_chunk_complete(self, raw_chunk: ModelResponse, state: Any, context: StreamingContext) -> None:
        """Forward every chunk at the end of processing."""
        await context.send(raw_chunk)

    # ------------------------------------------------------------------
    # LuthienPolicy interface (non-streaming methods)
    # ------------------------------------------------------------------

    async def process_request(self, request: Request, context: PolicyContext) -> Request:
        """Pass request through unchanged."""
        context.emit("event_driven_noop.request", "Request passed through")
        return request

    async def process_full_response(self, response: ModelResponse, context: PolicyContext) -> ModelResponse:
        """Pass full response through unchanged."""
        context.emit("event_driven_noop.response", "Full response passed through")
        return response


__all__ = ["EventDrivenNoOpPolicy"]
