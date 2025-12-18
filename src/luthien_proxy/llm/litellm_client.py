"""LiteLLM client implementation."""

from collections.abc import AsyncIterator
from typing import cast

import litellm
from litellm.types.utils import ModelResponse
from opentelemetry import trace

from luthien_proxy.llm.client import LLMClient
from luthien_proxy.llm.types import Request

# Allow LiteLLM to pass through unknown models without validation
# This is needed for new OpenAI models that LiteLLM doesn't recognize yet
litellm.drop_params = True

tracer = trace.get_tracer(__name__)


class LiteLLMClient(LLMClient):
    """LLM client using litellm library."""

    async def stream(self, request: Request) -> AsyncIterator[ModelResponse]:
        """Stream response from LLM."""
        with tracer.start_as_current_span("llm.stream") as span:
            span.set_attribute("llm.model", request.model)
            span.set_attribute("llm.stream", True)

            data = request.model_dump(exclude_none=True)
            data["stream"] = True
            response_stream = await litellm.acompletion(**data)
            # litellm returns AsyncIterator when stream=True
            return cast(AsyncIterator[ModelResponse], response_stream)

    async def complete(self, request: Request) -> ModelResponse:
        """Get complete response from LLM."""
        with tracer.start_as_current_span("llm.complete") as span:
            span.set_attribute("llm.model", request.model)
            span.set_attribute("llm.stream", False)

            data = request.model_dump(exclude_none=True)
            data["stream"] = False
            response = await litellm.acompletion(**data)
            return cast(ModelResponse, response)


__all__ = ["LiteLLMClient"]
