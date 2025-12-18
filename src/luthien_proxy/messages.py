"""Request type for policy processing.

This module re-exports the Request type from its canonical location.
For new code, prefer importing from luthien_proxy.llm.types directly.

Policies operate on:
- Request: The request sent to the LLM (OpenAI format) - our type
- ModelResponse: Complete LLM responses (non-streaming) - LiteLLM's type
- ModelResponse: Streaming chunks - also LiteLLM's type (same type, different usage)
"""

from luthien_proxy.llm.types import Request

__all__ = ["Request"]
