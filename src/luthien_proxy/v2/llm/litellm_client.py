# ABOUTME: LiteLLMClient implementation using litellm library
# ABOUTME: Provides stream() and complete() methods wrapping litellm.acompletion

"""LiteLLM client implementation."""

from collections.abc import AsyncIterator
from typing import cast

import aiohttp
import litellm
from litellm.types.utils import ModelResponse

from luthien_proxy.v2.llm.client import LLMClient
from luthien_proxy.v2.messages import Request


class LiteLLMClient(LLMClient):
    """LLM client using litellm library.

    Uses a shared aiohttp.ClientSession for efficient connection pooling.
    The session MUST be managed at the application level (e.g., FastAPI lifespan).
    """

    def __init__(self, shared_session: aiohttp.ClientSession):
        """Initialize LiteLLMClient with mandatory shared session.

        Args:
            shared_session: aiohttp.ClientSession for HTTP connection pooling.

        Raises:
            TypeError: If shared_session is not an aiohttp.ClientSession instance.
        """
        if not isinstance(shared_session, aiohttp.ClientSession):
            raise TypeError(f"shared_session must be aiohttp.ClientSession, got {type(shared_session).__name__}. ")
        self.shared_session = shared_session

    async def stream(self, request: Request) -> AsyncIterator[ModelResponse]:
        """Stream response from LLM."""
        data = request.model_dump(exclude_none=True)
        data["stream"] = True
        data["shared_session"] = self.shared_session
        response_stream = await litellm.acompletion(**data)
        # litellm returns AsyncIterator when stream=True
        return cast(AsyncIterator[ModelResponse], response_stream)

    async def complete(self, request: Request) -> ModelResponse:
        """Get complete response from LLM."""
        data = request.model_dump(exclude_none=True)
        data["stream"] = False
        data["shared_session"] = self.shared_session
        response = await litellm.acompletion(**data)
        return cast(ModelResponse, response)


__all__ = ["LiteLLMClient"]
