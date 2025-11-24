# ABOUTME: TransactionRecorder provides interface for recording request/response transactions
# ABOUTME: Includes NoOp implementation for testing and Default implementation for production

"""Module docstring."""

from abc import ABC, abstractmethod

from litellm.types.utils import ModelResponse
from opentelemetry import metrics

from luthien_proxy.messages import Request
from luthien_proxy.observability.context import ObservabilityContext
from luthien_proxy.storage.events import reconstruct_full_response_from_chunks


class TransactionRecorder(ABC):
    """Abstract interface for recording transactions."""

    @abstractmethod
    def __init__(self, observability: ObservabilityContext | None = None, max_chunks_queued: int = 4096):
        """Initialize transaction recorder.

        All subclasses must accept this parameter to ensure compatibility
        with PolicyOrchestrator's class-based instantiation.
        """

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
    async def record_response(self, original_response: ModelResponse, final_response: ModelResponse) -> None:
        """Record original and final response (non-streaming)."""

    @abstractmethod
    async def finalize_streaming_response(self) -> None:
        """Finalize streaming response recording (reconstruct from buffered chunks)."""


class NoOpTransactionRecorder(TransactionRecorder):
    """No-op recorder for testing."""

    def __init__(self, observability: ObservabilityContext | None = None, max_chunks_queued: int = 4096):  # noqa: D107, ARG002
        # Observability is accepted for signature compatibility but unused
        pass

    async def record_request(self, original: Request, final: Request) -> None:  # noqa: D102
        pass

    def add_ingress_chunk(self, chunk: ModelResponse) -> None:  # noqa: D102
        pass

    def add_egress_chunk(self, chunk: ModelResponse) -> None:  # noqa: D102
        pass

    async def record_response(self, original_response: ModelResponse, final_response: ModelResponse) -> None:  # noqa: D102
        pass

    async def finalize_streaming_response(self) -> None:  # noqa: D102
        pass


class DefaultTransactionRecorder(TransactionRecorder):
    """Default implementation using ObservabilityContext."""

    def __init__(self, observability: ObservabilityContext, max_chunks_queued: int = 4096):  # noqa: D107
        self.observability = observability
        self._ingress_chunks: list[ModelResponse] = []
        self._egress_chunks: list[ModelResponse] = []
        self._max_chunks_queued = max_chunks_queued

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

        self.observability.span.set_attribute("request.model", final.model)
        self.observability.span.set_attribute("request.message_count", len(final.messages))

    def add_ingress_chunk(self, chunk: ModelResponse) -> None:
        """Buffer ingress chunk."""
        if len(self._ingress_chunks) >= self._max_chunks_queued:
            self.observability.emit_event_nonblocking(
                event_type="transaction.recorder.ingress_truncated",
                data={"reason": f"max_chunks_queued_exceeded {len(self._ingress_chunks)} > {self._max_chunks_queued}"},
            )
            return
        self._ingress_chunks.append(chunk)

    def add_egress_chunk(self, chunk: ModelResponse) -> None:
        """Buffer egress chunk."""
        if len(self._egress_chunks) >= self._max_chunks_queued:
            self.observability.emit_event_nonblocking(
                event_type="transaction.recorder.egress_truncated",
                data={"reason": f"max_chunks_queued_exceeded {len(self._egress_chunks)} > {self._max_chunks_queued}"},
            )
            return
        self._egress_chunks.append(chunk)

    async def record_response(self, original_response: ModelResponse, final_response: ModelResponse) -> None:
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
            self.observability.span.set_attribute("response.finish_reason", finish_reason)

    async def finalize_streaming_response(self) -> None:
        """Reconstruct full responses from chunks and emit."""
        original_response_dict = reconstruct_full_response_from_chunks(self._ingress_chunks)
        final_response_dict = reconstruct_full_response_from_chunks(self._egress_chunks)

        await self.observability.emit_event(
            event_type="transaction.streaming_response_recorded",
            data={
                "ingress_chunks": len(self._ingress_chunks),
                "egress_chunks": len(self._egress_chunks),
                "original_response": original_response_dict,
                "final_response": final_response_dict,
            },
        )

        # Record chunk counts as OTel metrics
        meter = metrics.get_meter(__name__)
        ingress_counter = meter.create_counter("response.chunks.ingress")
        egress_counter = meter.create_counter("response.chunks.egress")
        # Note: observability context should expose transaction_id, but for now just record counts
        ingress_counter.add(len(self._ingress_chunks))
        egress_counter.add(len(self._egress_chunks))

    def _get_finish_reason(self, response: ModelResponse) -> str | None:
        """Extract finish_reason from response."""
        choices = response.model_dump().get("choices", [])
        return choices[0].get("finish_reason") if choices else None
