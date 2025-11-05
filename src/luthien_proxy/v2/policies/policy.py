# ABOUTME: Base Policy interface with full streaming control
# ABOUTME: Provides hooks for request, chunk events, content/tool call completion, and non-streaming responses

"""Module docstring."""

from __future__ import annotations

from abc import ABC
from typing import TYPE_CHECKING

from luthien_proxy.v2.observability.context import NoOpObservabilityContext

if TYPE_CHECKING:
    from litellm.types.utils import ModelResponse
    from opentelemetry.trace import Span

    from luthien_proxy.v2.messages import Request
    from luthien_proxy.v2.observability.context import ObservabilityContext
    from luthien_proxy.v2.streaming.streaming_policy_context import (
        StreamingPolicyContext,
    )


class PolicyContext:
    """Context for non-streaming policy operations.

    Provides access to:
    - call_id: Unique identifier for this request/response
    - span: OpenTelemetry span for tracing
    - request: The request being processed
    - observability: For emitting events that show up in activity monitor and traces
    """

    def __init__(self, call_id: str, span: Span, request: Request, observability: ObservabilityContext | None = None):
        """Initialize PolicyContext."""
        # TODO: call_id -> transaction_id

        self.call_id = call_id
        self.span = span
        self.request = request
        self.observability: ObservabilityContext = observability or NoOpObservabilityContext(call_id, span)


class Policy(ABC):
    """Base policy class with full streaming control.

    Subclasses can override __init__ to accept configuration parameters,
    which will be passed from the YAML config file. For example:

    ```python
    class MyPolicy(Policy):
        def __init__(self, threshold: float = 0.5, enabled: bool = True):
            self.threshold = threshold
            self.enabled = enabled
    ```

    The corresponding YAML config would be:
    ```yaml
    policy:
      class: "module.path:MyPolicy"
      config:
        threshold: 0.7
        enabled: true
    ```
    """

    async def on_request(self, request: Request, context: PolicyContext) -> Request:
        """Process request before sending to LLM."""
        return request

    async def on_chunk_received(self, ctx: StreamingPolicyContext) -> None:
        """Called on every chunk."""
        pass

    async def on_content_delta(self, ctx: StreamingPolicyContext) -> None:
        """Called when content delta received."""
        pass

    async def on_content_complete(self, ctx: StreamingPolicyContext) -> None:
        """Called when content block completes."""
        pass

    async def on_tool_call_delta(self, ctx: StreamingPolicyContext) -> None:
        """Called when tool call delta received."""
        pass

    async def on_tool_call_complete(self, ctx: StreamingPolicyContext) -> None:
        """Called when tool call block completes."""
        pass

    async def on_finish_reason(self, ctx: StreamingPolicyContext) -> None:
        """Called when finish_reason received."""
        pass

    async def on_stream_complete(self, ctx: StreamingPolicyContext) -> None:
        """Called when stream completes."""
        pass

    async def process_full_response(self, response: ModelResponse, context: PolicyContext) -> ModelResponse:
        """Process non-streaming response."""
        return response


__all__ = ["Policy", "PolicyContext"]
