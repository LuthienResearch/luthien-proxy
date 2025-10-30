# ABOUTME: LiteLLMClient implementation using litellm library
# ABOUTME: Provides stream() and complete() methods wrapping litellm.acompletion

"""LiteLLM client implementation."""

from collections.abc import AsyncIterator
from typing import cast

import litellm
from litellm.llms.custom_httpx.async_client_cleanup import close_litellm_async_clients
from litellm.types.utils import ModelResponse

from luthien_proxy.v2.llm.client import LLMClient
from luthien_proxy.v2.messages import Request


class LiteLLMClient(LLMClient):
    """LLM client using litellm library.

    Supports async context manager pattern for automatic cleanup:
        async with LiteLLMClient() as client:
            response = await client.complete(request)
        # HTTP connections automatically closed
    """

    async def stream(self, request: Request) -> AsyncIterator[ModelResponse]:
        """Stream response from LLM."""
        data = request.model_dump(exclude_none=True)
        data["stream"] = True
        response_stream = await litellm.acompletion(**data)
        # litellm returns AsyncIterator when stream=True
        return cast(AsyncIterator[ModelResponse], response_stream)

    async def complete(self, request: Request) -> ModelResponse:
        """Get complete response from LLM."""
        data = request.model_dump(exclude_none=True)
        data["stream"] = False
        response = await litellm.acompletion(**data)
        return cast(ModelResponse, response)

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, *_args):
        """Async context manager exit - cleanup HTTP clients."""
        await self.cleanup()
        return False

    @staticmethod
    async def cleanup():
        """Close all cached async HTTP clients to prevent resource leaks.

        This should be called at the end of the application lifecycle or
        in test teardown to ensure all HTTP connections are properly closed.
        """
        await close_litellm_async_clients()


__all__ = ["LiteLLMClient"]
