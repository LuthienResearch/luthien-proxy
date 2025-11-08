# ABOUTME: LiteLLMClient implementation using litellm library
# ABOUTME: Provides stream() and complete() methods wrapping litellm.acompletion

"""LiteLLM client implementation."""

from collections.abc import AsyncIterator
from typing import cast

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
        response_stream = await litellm.acompletion(**data)
        # litellm returns AsyncIterator when stream=True
        return cast(AsyncIterator[ModelResponse], response_stream)

    async def complete(self, request: Request) -> ModelResponse:
        """Get complete response from LLM."""
        data = request.model_dump(exclude_none=True)
        data["stream"] = False
        response = await litellm.acompletion(**data)
        return cast(ModelResponse, response)


__all__ = ["LiteLLMClient"]
