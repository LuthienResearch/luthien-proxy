# ABOUTME: LiteLLMClient implementation using litellm library
# ABOUTME: Provides stream() and complete() methods wrapping litellm.acompletion

"""LiteLLM client implementation."""

from typing import AsyncIterator, cast

import litellm
from litellm.types.utils import ModelResponse

from luthien_proxy.v2.llm.client import LLMClient
from luthien_proxy.v2.messages import Request


class LiteLLMClient(LLMClient):
    """LLM client using litellm library."""

    async def stream(self, request: Request) -> AsyncIterator[ModelResponse]:
        """Stream response from LLM."""
        data = request.model_dump(exclude_none=True)
        data["stream"] = True
        response = await litellm.acompletion(**data)
        async for chunk in response:
            yield chunk

    async def complete(self, request: Request) -> ModelResponse:
        """Get complete response from LLM."""
        data = request.model_dump(exclude_none=True)
        data["stream"] = False
        response = await litellm.acompletion(**data)
        return cast(ModelResponse, response)


__all__ = ["LiteLLMClient"]
