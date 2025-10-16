# ABOUTME: No-op policy implementation - passes everything through unchanged
# ABOUTME: Useful for testing and as a base for development

"""No-op policy that passes all requests and responses through unchanged."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from luthien_proxy.v2.control.models import StreamAction
from luthien_proxy.v2.policies.base import PolicyHandler, StreamControl

if TYPE_CHECKING:
    from typing import Any

    ModelResponse = Any  # LiteLLM's ModelResponse has incomplete type annotations


class NoOpPolicy(PolicyHandler):
    """Policy that does nothing - passes everything through unchanged.

    Useful for:
    - Testing the proxy without policy interference
    - Baseline performance measurements
    - Development and debugging
    """

    def __init__(self):
        """Initialize no-op policy."""
        super().__init__()

    async def apply_request_policies(self, data: dict) -> dict:
        """Pass request through unchanged."""
        return data

    async def apply_response_policy(self, response: ModelResponse) -> ModelResponse:
        """Pass response through unchanged."""
        return response

    async def apply_streaming_chunk_policy(
        self,
        chunk: ModelResponse,
        outgoing_queue: asyncio.Queue,
        control: StreamControl,
    ) -> StreamAction:
        """Pass streaming chunk through unchanged."""
        await outgoing_queue.put(chunk)
        return StreamAction.CONTINUE


__all__ = ["NoOpPolicy"]
