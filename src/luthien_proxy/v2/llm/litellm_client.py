# ABOUTME: LiteLLMClient implementation using litellm library
# ABOUTME: Provides stream() and complete() methods wrapping litellm.acompletion

"""LiteLLM client implementation."""

from collections.abc import AsyncGenerator
from typing import AsyncIterator, cast

import litellm
from litellm.types.utils import ModelResponse

from luthien_proxy.v2.llm.client import LLMClient
from luthien_proxy.v2.messages import Request


class LiteLLMClient(LLMClient):
    """LLM client using litellm library."""

    async def stream(self, request: Request) -> AsyncGenerator[ModelResponse, None]:
        """Stream response from LLM."""
        data = request.model_dump(exclude_none=True)
        data["stream"] = True
        response_stream = await litellm.acompletion(**data)
        # litellm returns AsyncIterator when stream=True
        response_stream = cast(AsyncIterator[ModelResponse], response_stream)
        async for chunk in response_stream:
            yield chunk

    async def complete(self, request: Request) -> ModelResponse:
        """Get complete response from LLM."""
        data = request.model_dump(exclude_none=True)
        data["stream"] = False
        response = await litellm.acompletion(**data)
        return cast(ModelResponse, response)


__all__ = ["LiteLLMClient"]
