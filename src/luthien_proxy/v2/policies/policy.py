# ABOUTME: Base Policy interface with full streaming control
# ABOUTME: Provides hooks for request, chunk events, content/tool call completion, and non-streaming responses

"""Module docstring."""

from __future__ import annotations

from abc import ABC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from litellm.types.utils import ModelResponse
    from opentelemetry.trace import Span

    from luthien_proxy.v2.messages import Request
    from luthien_proxy.v2.streaming.streaming_response_context import (
        StreamingResponseContext,
    )


class PolicyContext:
    """Context for non-streaming policy operations."""

    def __init__(self, call_id: str, span: Span, request: Request):  # noqa: D107
        self.call_id = call_id
        self.span = span
        self.request = request


class Policy(ABC):
    """Base policy class with full streaming control."""

    async def on_request(self, request: Request, context: PolicyContext) -> Request:
        """Process request before sending to LLM."""
        return request

    async def on_chunk_received(self, ctx: StreamingResponseContext) -> None:
        """Called on every chunk."""
        pass

    async def on_content_delta(self, ctx: StreamingResponseContext) -> None:
        """Called when content delta received."""
        pass

    async def on_content_complete(self, ctx: StreamingResponseContext) -> None:
        """Called when content block completes."""
        pass

    async def on_tool_call_delta(self, ctx: StreamingResponseContext) -> None:
        """Called when tool call delta received."""
        pass

    async def on_tool_call_complete(self, ctx: StreamingResponseContext) -> None:
        """Called when tool call block completes."""
        pass

    async def on_finish_reason(self, ctx: StreamingResponseContext) -> None:
        """Called when finish_reason received."""
        pass

    async def on_stream_complete(self, ctx: StreamingResponseContext) -> None:
        """Called when stream completes."""
        pass

    async def process_full_response(self, response: ModelResponse, context: PolicyContext) -> ModelResponse:
        """Process non-streaming response."""
        return response


__all__ = ["Policy", "PolicyContext"]
