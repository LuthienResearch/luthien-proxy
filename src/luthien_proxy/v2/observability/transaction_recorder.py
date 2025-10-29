# ABOUTME: TransactionRecorder provides interface for recording request/response transactions
# ABOUTME: Includes NoOp implementation for testing and Default implementation for production

"""Module docstring."""

from abc import ABC, abstractmethod

from litellm.types.utils import ModelResponse

from luthien_proxy.v2.messages import Request
from luthien_proxy.v2.observability.context import ObservabilityContext


class TransactionRecorder(ABC):
    """Abstract interface for recording transactions."""

    @abstractmethod
    async def record_request(self, original: Request, final: Request) -> None:
        """Record original and final request."""

    @abstractmethod
    def add_ingress_chunk(self, chunk: ModelResponse) -> None:
        """Buffer ingress chunk (streaming only)."""

    @abstractmethod
    def add_egress_chunk(self, chunk: ModelResponse) -> None:
        """Buffer egress chunk (streaming only)."""

    @abstractmethod
    async def finalize_streaming(self) -> None:
        """Finalize streaming response recording."""

    @abstractmethod
    async def finalize_non_streaming(self, original_response: ModelResponse, final_response: ModelResponse) -> None:
        """Finalize non-streaming response recording."""


class NoOpTransactionRecorder(TransactionRecorder):
    """No-op recorder for testing."""

    async def record_request(self, original: Request, final: Request) -> None:  # noqa: D102
        pass

    def add_ingress_chunk(self, chunk: ModelResponse) -> None:  # noqa: D102
        pass

    def add_egress_chunk(self, chunk: ModelResponse) -> None:  # noqa: D102
        pass

    async def finalize_streaming(self) -> None:  # noqa: D102
        pass

    async def finalize_non_streaming(self, original_response: ModelResponse, final_response: ModelResponse) -> None:  # noqa: D102
        pass


class DefaultTransactionRecorder(TransactionRecorder):
    """Default implementation using ObservabilityContext."""

    def __init__(self, observability: ObservabilityContext):  # noqa: D107
        self.observability = observability
        self.ingress_chunks: list[ModelResponse] = []
        self.egress_chunks: list[ModelResponse] = []

    async def record_request(self, original: Request, final: Request) -> None:
        """Record request via observability context."""
        await self.observability.emit_event(
            event_type="transaction.request_recorded",
            data={
                "original_model": original.model,
                "final_model": final.model,
                "original_request": original.model_dump(exclude_none=True),
                "final_request": final.model_dump(exclude_none=True),
            },
        )

        self.observability.add_span_attribute("request.model", final.model)
        self.observability.add_span_attribute("request.message_count", len(final.messages))

    def add_ingress_chunk(self, chunk: ModelResponse) -> None:
        """Buffer ingress chunk."""
        self.ingress_chunks.append(chunk)

    def add_egress_chunk(self, chunk: ModelResponse) -> None:
        """Buffer egress chunk."""
        self.egress_chunks.append(chunk)

    async def finalize_streaming(self) -> None:
        """Reconstruct full responses from chunks and emit."""
        from luthien_proxy.v2.storage.events import (
            reconstruct_full_response_from_chunks,
        )

        original_response_dict = reconstruct_full_response_from_chunks(self.ingress_chunks)
        final_response_dict = reconstruct_full_response_from_chunks(self.egress_chunks)

        await self.observability.emit_event(
            event_type="transaction.streaming_response_recorded",
            data={
                "ingress_chunks": len(self.ingress_chunks),
                "egress_chunks": len(self.egress_chunks),
                "original_response": original_response_dict,
                "final_response": final_response_dict,
            },
        )

        self.observability.record_metric("response.chunks.ingress", len(self.ingress_chunks))
        self.observability.record_metric("response.chunks.egress", len(self.egress_chunks))

    async def finalize_non_streaming(self, original_response: ModelResponse, final_response: ModelResponse) -> None:
        """Emit full responses directly."""
        await self.observability.emit_event(
            event_type="transaction.non_streaming_response_recorded",
            data={
                "original_finish_reason": self._get_finish_reason(original_response),
                "final_finish_reason": self._get_finish_reason(final_response),
                "original_response": original_response.model_dump(),
                "final_response": final_response.model_dump(),
            },
        )

        finish_reason = self._get_finish_reason(final_response)
        if finish_reason:
            self.observability.add_span_attribute("response.finish_reason", finish_reason)

    def _get_finish_reason(self, response: ModelResponse) -> str | None:
        """Extract finish_reason from response."""
        choices = response.model_dump().get("choices", [])
        return choices[0].get("finish_reason") if choices else None
