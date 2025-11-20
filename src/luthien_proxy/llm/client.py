# ABOUTME: LLMClient abstract interface for LLM backend communication
# ABOUTME: Defines stream() and complete() methods for streaming and non-streaming responses

"""Abstract LLM client interface for backend communication.

Defines the LLMClient abstract base class with stream() and complete() methods
for streaming and non-streaming LLM responses respectively.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from litellm.types.utils import ModelResponse

    from luthien_proxy.messages import Request


class LLMClient(ABC):
    """Abstract interface for LLM backend communication."""

    @abstractmethod
    async def stream(self, request: Request) -> AsyncIterator[ModelResponse]:
        """Stream response from LLM backend (OpenAI format)."""

    @abstractmethod
    async def complete(self, request: Request) -> ModelResponse:
        """Get complete response from LLM backend (OpenAI format)."""


__all__ = ["LLMClient"]
