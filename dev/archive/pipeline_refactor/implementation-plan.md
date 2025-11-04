# Pipeline Refactor v4 - Implementation Plan

**Date:** 2025-10-29
**Status:** Ready for Implementation

---

## Overview

This plan implements the v4 pipeline refactor with:
- ObservabilityContext for unified observability
- SimplePolicy for easy policy authoring
- TransactionRecorder abstraction
- Block dispatch mapping
- Comprehensive testing (unit + e2e)

**Philosophy:**
- **Fail fast**: Strong assumptions, raise errors when violated
- **Precise typing**: No `Any` types, explicit annotations
- **Short functions**: Max ~20 lines per function
- **No defensive coding**: No "just in case" logic
- **Legible**: Clear names, obvious flow

---

## File Structure

```
src/luthien_proxy/v2/
├── observability/
│   ├── __init__.py
│   ├── context.py              # ObservabilityContext ABC + implementations
│   └── transaction_recorder.py # TransactionRecorder ABC + implementations
├── llm/
│   ├── __init__.py
│   ├── client.py               # LLMClient ABC
│   └── litellm_client.py       # LiteLLMClient implementation
├── orchestration/
│   ├── __init__.py
│   ├── policy_orchestrator.py  # PolicyOrchestrator
│   └── factory.py              # create_default_orchestrator factory
├── policies/
│   ├── __init__.py
│   ├── policy.py               # Policy ABC
│   └── simple_policy.py        # SimplePolicy implementation
├── streaming/
│   ├── stream_state.py         # Add raw_chunks + last_emission_index
│   ├── streaming_chunk_assembler.py # Store raw_chunks
│   ├── streaming_response_context.py # Add observability field
│   └── helpers.py              # send_text, send_chunk, passthrough helpers
└── gateway_routes.py           # Update to use PolicyOrchestrator

tests/unit_tests/v2/
├── observability/
│   ├── test_context.py
│   └── test_transaction_recorder.py
├── llm/
│   └── test_litellm_client.py
├── orchestration/
│   ├── test_policy_orchestrator_request.py
│   ├── test_policy_orchestrator_streaming.py
│   └── test_policy_orchestrator_non_streaming.py
├── policies/
│   └── test_simple_policy.py
└── streaming/
    └── test_helpers.py

tests/e2e_tests/v2/
├── test_streaming_openai.py
├── test_streaming_anthropic.py
├── test_non_streaming_openai.py
├── test_non_streaming_anthropic.py
├── test_tool_calls_openai.py
└── test_tool_calls_anthropic.py
```

---

## Implementation Order

### Phase 1: Core Abstractions (No Dependencies)

#### 1.1 ObservabilityContext

**File:** `src/luthien_proxy/v2/observability/context.py`

```python
from abc import ABC, abstractmethod
from typing import Any
from opentelemetry.trace import Span


class ObservabilityContext(ABC):
    """Unified interface for observability operations."""

    @property
    @abstractmethod
    def transaction_id(self) -> str:
        """Transaction ID for this context."""

    @abstractmethod
    async def emit_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Emit event with automatic context enrichment."""

    @abstractmethod
    def record_metric(
        self, name: str, value: float, labels: dict[str, str] | None = None
    ) -> None:
        """Record metric with automatic labels."""

    @abstractmethod
    def add_span_attribute(self, key: str, value: Any) -> None:
        """Add attribute to current span."""

    @abstractmethod
    def add_span_event(
        self, name: str, attributes: dict[str, Any] | None = None
    ) -> None:
        """Add event to current span."""


class NoOpObservabilityContext(ObservabilityContext):
    """No-op implementation for testing."""

    def __init__(self, transaction_id: str):
        self._transaction_id = transaction_id

    @property
    def transaction_id(self) -> str:
        return self._transaction_id

    async def emit_event(self, event_type: str, data: dict[str, Any]) -> None:
        pass

    def record_metric(
        self, name: str, value: float, labels: dict[str, str] | None = None
    ) -> None:
        pass

    def add_span_attribute(self, key: str, value: Any) -> None:
        pass

    def add_span_event(
        self, name: str, attributes: dict[str, Any] | None = None
    ) -> None:
        pass


class DefaultObservabilityContext(ObservabilityContext):
    """Default implementation using OTel + DB + Redis."""

    def __init__(
        self,
        transaction_id: str,
        span: Span,
        db_pool: "DatabasePool | None" = None,
        event_publisher: "RedisEventPublisher | None" = None,
    ):
        self._transaction_id = transaction_id
        self.span = span
        self.db_pool = db_pool
        self.event_publisher = event_publisher

    @property
    def transaction_id(self) -> str:
        return self._transaction_id

    async def emit_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Emit to DB, Redis, and OTel span."""
        import time

        enriched_data = {
            "call_id": self._transaction_id,
            "timestamp": time.time(),
            "trace_id": format(self.span.get_span_context().trace_id, "032x"),
            "span_id": format(self.span.get_span_context().span_id, "016x"),
            **data,
        }

        if self.db_pool:
            from luthien_proxy.v2.storage.events import emit_custom_event

            await emit_custom_event(
                call_id=self._transaction_id,
                event_type=event_type,
                data=enriched_data,
                db_pool=self.db_pool,
            )

        if self.event_publisher:
            await self.event_publisher.publish_event(
                call_id=self._transaction_id, event_type=event_type, data=data
            )

        self.add_span_event(event_type, data)

    def record_metric(
        self, name: str, value: float, labels: dict[str, str] | None = None
    ) -> None:
        """Record metric with automatic labels."""
        from opentelemetry import metrics

        all_labels = {"call_id": self._transaction_id, **(labels or {})}
        meter = metrics.get_meter(__name__)
        counter = meter.create_counter(name)
        counter.add(value, all_labels)

    def add_span_attribute(self, key: str, value: Any) -> None:
        """Add attribute to current span."""
        self.span.set_attribute(key, value)

    def add_span_event(
        self, name: str, attributes: dict[str, Any] | None = None
    ) -> None:
        """Add event to current span."""
        self.span.add_event(name, attributes or {})
```

**Testing:**
- Unit test: `NoOpObservabilityContext` behaves correctly
- Unit test: `DefaultObservabilityContext` enriches data correctly
- Unit test: `DefaultObservabilityContext` calls DB/Redis/OTel correctly (with mocks)

**Acceptance:**
- [ ] `NoOpObservabilityContext` passes all interface methods
- [ ] `DefaultObservabilityContext` enriches with call_id/trace_id/timestamp
- [ ] `DefaultObservabilityContext` emits to DB when db_pool provided
- [ ] `DefaultObservabilityContext` emits to Redis when event_publisher provided
- [ ] `DefaultObservabilityContext` adds span events
- [ ] All tests pass with 100% coverage

#### 1.2 TransactionRecorder

**File:** `src/luthien_proxy/v2/observability/transaction_recorder.py`

```python
from abc import ABC, abstractmethod
from litellm.types.utils import ModelResponse
from luthien_proxy.v2.types import RequestMessage
from luthien_proxy.v2.observability.context import ObservabilityContext


class TransactionRecorder(ABC):
    """Abstract interface for recording transactions."""

    @abstractmethod
    async def record_request(
        self, original: RequestMessage, final: RequestMessage
    ) -> None:
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
    async def finalize_non_streaming(
        self, original_response: ModelResponse, final_response: ModelResponse
    ) -> None:
        """Finalize non-streaming response recording."""


class NoOpTransactionRecorder(TransactionRecorder):
    """No-op recorder for testing."""

    async def record_request(
        self, original: RequestMessage, final: RequestMessage
    ) -> None:
        pass

    def add_ingress_chunk(self, chunk: ModelResponse) -> None:
        pass

    def add_egress_chunk(self, chunk: ModelResponse) -> None:
        pass

    async def finalize_streaming(self) -> None:
        pass

    async def finalize_non_streaming(
        self, original_response: ModelResponse, final_response: ModelResponse
    ) -> None:
        pass


class DefaultTransactionRecorder(TransactionRecorder):
    """Default implementation using ObservabilityContext."""

    def __init__(self, observability: ObservabilityContext):
        self.observability = observability
        self.ingress_chunks: list[ModelResponse] = []
        self.egress_chunks: list[ModelResponse] = []

    async def record_request(
        self, original: RequestMessage, final: RequestMessage
    ) -> None:
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
        self.observability.add_span_attribute(
            "request.message_count", len(final.messages)
        )

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

        original_response_dict = reconstruct_full_response_from_chunks(
            self.ingress_chunks
        )
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

        self.observability.record_metric(
            "response.chunks.ingress", len(self.ingress_chunks)
        )
        self.observability.record_metric(
            "response.chunks.egress", len(self.egress_chunks)
        )

    async def finalize_non_streaming(
        self, original_response: ModelResponse, final_response: ModelResponse
    ) -> None:
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
            self.observability.add_span_attribute(
                "response.finish_reason", finish_reason
            )

    def _get_finish_reason(self, response: ModelResponse) -> str | None:
        """Extract finish_reason from response."""
        choices = response.model_dump().get("choices", [])
        return choices[0].get("finish_reason") if choices else None
```

**Testing:**
- Unit test: `NoOpTransactionRecorder` does nothing
- Unit test: `DefaultTransactionRecorder.record_request` emits event
- Unit test: `DefaultTransactionRecorder.finalize_streaming` reconstructs correctly
- Unit test: `DefaultTransactionRecorder.finalize_non_streaming` emits full responses

**Acceptance:**
- [ ] `NoOpTransactionRecorder` implements all methods as no-ops
- [ ] `DefaultTransactionRecorder.record_request` emits via observability
- [ ] `DefaultTransactionRecorder` buffers chunks correctly
- [ ] `finalize_streaming` reconstructs and emits
- [ ] `finalize_non_streaming` emits full responses
- [ ] All tests pass with 100% coverage

### Phase 2: Update Existing Components

#### 2.1 Update StreamState

**File:** `src/luthien_proxy/v2/streaming/stream_state.py`

**Changes:**
```python
@dataclass
class StreamState:
    blocks: list[StreamBlock] = field(default_factory=list)
    current_block: StreamBlock | None = None
    just_completed: StreamBlock | None = None
    finish_reason: str | None = None
    raw_chunks: list[ModelResponse] = field(default_factory=list)  # NEW
    last_emission_index: int = 0  # NEW
```

**Testing:**
- Update existing tests to handle new fields
- Verify default values

**Acceptance:**
- [ ] `raw_chunks` defaults to empty list
- [ ] `last_emission_index` defaults to 0
- [ ] All existing tests pass

#### 2.2 Update StreamingChunkAssembler

**File:** `src/luthien_proxy/v2/streaming/streaming_chunk_assembler.py`

**Changes:** Add one line in `process` method:
```python
async def process(
    self,
    incoming: AsyncIterator[ModelResponse],
    context: Any,
) -> None:
    async for chunk in incoming:
        self.state.raw_chunks.append(chunk)  # NEW - add this line

        # Existing code continues...
        self._update_state(chunk)
        chunk = self._strip_empty_content(chunk)
        await self.on_chunk(chunk, self.state, context)
        self.state.just_completed = None
```

**Testing:**
- Unit test: Verify `raw_chunks` populated during processing
- Ensure existing tests still pass

**Acceptance:**
- [ ] `raw_chunks` populated with all chunks
- [ ] Chunks stored in correct order
- [ ] All existing tests pass

#### 2.3 Update StreamingResponseContext

**File:** `src/luthien_proxy/v2/streaming/streaming_response_context.py`

**Changes:**
```python
from dataclasses import dataclass
from typing import Any
from luthien_proxy.v2.observability.context import ObservabilityContext


@dataclass
class StreamingResponseContext:
    """Context for policy invocations during streaming."""

    transaction_id: str
    final_request: "RequestMessage"
    ingress_assembler: "StreamingChunkAssembler | None"
    egress_queue: "asyncio.Queue[ModelResponse]"
    scratchpad: dict[str, Any]
    observability: ObservabilityContext  # NEW

    @property
    def ingress_state(self) -> "StreamState":
        """Current ingress state."""
        if self.ingress_assembler is None:
            raise RuntimeError("ingress_assembler not yet initialized")
        return self.ingress_assembler.state
```

**Testing:**
- Unit test: Property guard raises when assembler is None
- Unit test: Property returns state when assembler exists

**Acceptance:**
- [ ] `observability` field present
- [ ] `ingress_state` property raises when assembler None
- [ ] `ingress_state` property returns state when assembler set
- [ ] All tests pass

#### 2.4 Create Helper Functions

**File:** `src/luthien_proxy/v2/streaming/helpers.py`

```python
from litellm.types.utils import ModelResponse
from luthien_proxy.v2.streaming.streaming_response_context import (
    StreamingResponseContext,
)
from luthien_proxy.v2.policies.utils import create_text_chunk, create_tool_call_chunk


async def send_text(ctx: StreamingResponseContext, text: str) -> None:
    """Send text chunk to egress."""
    chunk = create_text_chunk(text)
    await ctx.egress_queue.put(chunk)


async def send_chunk(ctx: StreamingResponseContext, chunk: ModelResponse) -> None:
    """Send chunk to egress."""
    await ctx.egress_queue.put(chunk)


def get_last_ingress_chunk(ctx: StreamingResponseContext) -> ModelResponse | None:
    """Get most recent ingress chunk."""
    chunks = ctx.ingress_state.raw_chunks
    return chunks[-1] if chunks else None


async def passthrough_last_chunk(ctx: StreamingResponseContext) -> None:
    """Passthrough most recent ingress chunk to egress."""
    chunk = get_last_ingress_chunk(ctx)
    if chunk:
        await send_chunk(ctx, chunk)


async def passthrough_accumulated_chunks(ctx: StreamingResponseContext) -> None:
    """
    Emit all chunks buffered since last emission.

    Preserves original chunk timing when content unchanged.
    """
    start_idx = ctx.ingress_state.last_emission_index
    chunks = ctx.ingress_state.raw_chunks[start_idx:]

    for chunk in chunks:
        await send_chunk(ctx, chunk)

    ctx.ingress_state.last_emission_index = len(ctx.ingress_state.raw_chunks)


async def send_tool_call(
    ctx: StreamingResponseContext, tool_call: "ToolCall"
) -> None:
    """Send complete tool call as chunk."""
    chunk = create_tool_call_chunk(tool_call)
    await ctx.egress_queue.put(chunk)
```

**Testing:**
- Unit test: Each helper function works correctly
- Unit test: `passthrough_accumulated_chunks` updates index correctly

**Acceptance:**
- [ ] `send_text` creates and queues text chunk
- [ ] `send_chunk` queues chunk
- [ ] `get_last_ingress_chunk` returns last or None
- [ ] `passthrough_last_chunk` works
- [ ] `passthrough_accumulated_chunks` emits correct range and updates index
- [ ] `send_tool_call` creates and queues tool call chunk
- [ ] All tests pass with 100% coverage

### Phase 3: Policy Abstractions

#### 3.1 Base Policy Interface

**File:** `src/luthien_proxy/v2/policies/policy.py`

```python
from abc import ABC
from litellm.types.utils import ModelResponse
from luthien_proxy.v2.types import RequestMessage
from luthien_proxy.v2.streaming.streaming_response_context import (
    StreamingResponseContext,
)


class PolicyContext:
    """Context for non-streaming policy operations."""

    def __init__(self, call_id: str, span: "Span", request: RequestMessage):
        self.call_id = call_id
        self.span = span
        self.request = request


class Policy(ABC):
    """Base policy class with full streaming control."""

    async def on_request(
        self, request: RequestMessage, context: PolicyContext
    ) -> RequestMessage:
        """Process request before sending to LLM."""
        return request

    async def on_chunk_received(self, ctx: StreamingResponseContext) -> None:
        """Called on every chunk."""
        pass

    async def on_content_delta(self, ctx: StreamingResponseContext) -> None:
        """Called when content delta received."""
        pass

    async def on_content_complete(self, ctx: StreamingResponseContext) -> None:
        """Called when content block completes."""
        pass

    async def on_tool_call_delta(self, ctx: StreamingResponseContext) -> None:
        """Called when tool call delta received."""
        pass

    async def on_tool_call_complete(self, ctx: StreamingResponseContext) -> None:
        """Called when tool call block completes."""
        pass

    async def on_finish_reason(self, ctx: StreamingResponseContext) -> None:
        """Called when finish_reason received."""
        pass

    async def on_stream_complete(self, ctx: StreamingResponseContext) -> None:
        """Called when stream completes."""
        pass

    async def process_full_response(
        self, response: ModelResponse, context: PolicyContext
    ) -> ModelResponse:
        """Process non-streaming response."""
        return response
```

**Testing:**
- Unit test: Default implementations do nothing (no-op)

**Acceptance:**
- [ ] All methods have default implementations
- [ ] Subclass can override any method
- [ ] Tests pass

#### 3.2 SimplePolicy

**File:** `src/luthien_proxy/v2/policies/simple_policy.py`

```python
from luthien_proxy.v2.policies.policy import Policy
from luthien_proxy.v2.types import RequestMessage
from luthien_proxy.v2.streaming.streaming_response_context import (
    StreamingResponseContext,
)
from luthien_proxy.v2.streaming.helpers import (
    send_text,
    send_chunk,
    passthrough_accumulated_chunks,
    send_tool_call,
    get_last_ingress_chunk,
)


class SimplePolicy(Policy):
    """
    Convenience base class for content-level transformations.

    Buffers streaming content and applies transformations when complete.
    """

    # ===== Simple methods that subclasses override =====

    async def on_request_simple(self, request: RequestMessage) -> RequestMessage:
        """Transform/validate request before LLM."""
        return request

    async def on_response_content(
        self, content: str, request: RequestMessage
    ) -> str:
        """Transform complete response content."""
        return content

    async def on_response_tool_call(
        self, tool_call: "ToolCall", request: RequestMessage
    ) -> "ToolCall":
        """Transform/validate a complete tool call."""
        return tool_call

    # ===== Implementation of streaming hooks =====

    async def on_request(
        self, request: RequestMessage, context: "PolicyContext"
    ) -> RequestMessage:
        """Delegate to simple method."""
        return await self.on_request_simple(request)

    async def on_content_complete(self, ctx: StreamingResponseContext) -> None:
        """Transform content and emit."""
        # Get the completed content block
        if not ctx.ingress_state.just_completed:
            return

        from luthien_proxy.v2.streaming.stream_blocks import ContentStreamBlock

        block = ctx.ingress_state.just_completed
        if not isinstance(block, ContentStreamBlock):
            return

        content = block.content
        transformed = await self.on_response_content(content, ctx.final_request)

        if transformed != content:
            await send_text(ctx, transformed)
        else:
            await passthrough_accumulated_chunks(ctx)

    async def on_tool_call_complete(self, ctx: StreamingResponseContext) -> None:
        """Transform tool call and emit."""
        if not ctx.ingress_state.just_completed:
            return

        from luthien_proxy.v2.streaming.stream_blocks import ToolCallStreamBlock

        block = ctx.ingress_state.just_completed
        if not isinstance(block, ToolCallStreamBlock):
            return

        tool_call = block.tool_call
        transformed = await self.on_response_tool_call(tool_call, ctx.final_request)

        if transformed != tool_call:
            await send_tool_call(ctx, transformed)
        else:
            await passthrough_accumulated_chunks(ctx)

    async def on_content_delta(self, ctx: StreamingResponseContext) -> None:
        """Buffer deltas, don't emit yet."""
        pass  # Assembler buffers

    async def on_tool_call_delta(self, ctx: StreamingResponseContext) -> None:
        """Buffer tool call deltas, don't emit yet."""
        pass  # Assembler buffers

    async def on_chunk_received(self, ctx: StreamingResponseContext) -> None:
        """Pass through metadata chunks immediately."""
        chunk = get_last_ingress_chunk(ctx)
        if chunk and not self._has_content_or_tool_delta(chunk):
            await send_chunk(ctx, chunk)

    def _has_content_or_tool_delta(self, chunk: "ModelResponse") -> bool:
        """Check if chunk contains content or tool call delta."""
        if not chunk.choices:
            return False
        delta = chunk.choices[0].delta
        if not delta:
            return False
        return bool(delta.get("content") or delta.get("tool_calls"))
```

**Testing:**
- Unit test: SimplePolicy buffers content correctly
- Unit test: SimplePolicy emits transformed content
- Unit test: SimplePolicy passes through unchanged content with original chunks
- Unit test: SimplePolicy handles tool calls correctly
- Unit test: Metadata chunks pass through immediately

**Acceptance:**
- [ ] Content transformation works
- [ ] Unchanged content uses passthrough
- [ ] Tool call transformation works
- [ ] Metadata chunks forwarded immediately
- [ ] All tests pass with 100% coverage

### Phase 4: LLM Client

#### 4.1 LLMClient Interface

**File:** `src/luthien_proxy/v2/llm/client.py`

```python
from abc import ABC, abstractmethod
from typing import AsyncIterator
from litellm.types.utils import ModelResponse
from luthien_proxy.v2.types import RequestMessage


class LLMClient(ABC):
    """Abstract interface for LLM backend communication."""

    @abstractmethod
    async def stream(self, request: RequestMessage) -> AsyncIterator[ModelResponse]:
        """Stream response from LLM backend (OpenAI format)."""

    @abstractmethod
    async def complete(self, request: RequestMessage) -> ModelResponse:
        """Get complete response from LLM backend (OpenAI format)."""
```

**Testing:**
- Unit test: Mock implementation works

**Acceptance:**
- [ ] Interface defined
- [ ] Mock implementation for tests
- [ ] Tests pass

#### 4.2 LiteLLMClient Implementation

**File:** `src/luthien_proxy/v2/llm/litellm_client.py`

```python
from typing import AsyncIterator, cast
import litellm
from litellm.types.utils import ModelResponse
from luthien_proxy.v2.llm.client import LLMClient
from luthien_proxy.v2.types import RequestMessage


class LiteLLMClient(LLMClient):
    """LLM client using litellm library."""

    async def stream(self, request: RequestMessage) -> AsyncIterator[ModelResponse]:
        """Stream response from LLM."""
        data = request.model_dump(exclude_none=True)
        data["stream"] = True
        response = await litellm.acompletion(**data)
        async for chunk in response:
            yield chunk

    async def complete(self, request: RequestMessage) -> ModelResponse:
        """Get complete response from LLM."""
        data = request.model_dump(exclude_none=True)
        data["stream"] = False
        response = await litellm.acompletion(**data)
        return cast(ModelResponse, response)
```

**Testing:**
- Unit test: Stream method returns async iterator
- Unit test: Complete method returns ModelResponse
- Integration test: Works with real LiteLLM (optional, can mock)

**Acceptance:**
- [ ] Stream method works
- [ ] Complete method works
- [ ] Tests pass

### Phase 5: PolicyOrchestrator

#### 5.1 PolicyOrchestrator Implementation

**File:** `src/luthien_proxy/v2/orchestration/policy_orchestrator.py`

This is the most complex component. Break into small, testable methods.

```python
import asyncio
from typing import AsyncIterator, Callable
from opentelemetry import trace
from opentelemetry.trace import Span
from litellm.types.utils import ModelResponse

from luthien_proxy.v2.types import RequestMessage
from luthien_proxy.v2.policies.policy import Policy, PolicyContext
from luthien_proxy.v2.llm.client import LLMClient
from luthien_proxy.v2.observability.context import ObservabilityContext
from luthien_proxy.v2.observability.transaction_recorder import TransactionRecorder
from luthien_proxy.v2.streaming.streaming_orchestrator import StreamingOrchestrator
from luthien_proxy.v2.streaming.streaming_chunk_assembler import (
    StreamingChunkAssembler,
)
from luthien_proxy.v2.streaming.streaming_response_context import (
    StreamingResponseContext,
)
from luthien_proxy.v2.streaming.stream_state import StreamState
from luthien_proxy.v2.streaming.stream_blocks import (
    ContentStreamBlock,
    ToolCallStreamBlock,
)

tracer = trace.get_tracer(__name__)


class PolicyOrchestrator:
    """Orchestrates request/response flow through policy layer."""

    def __init__(
        self,
        policy: Policy,
        llm_client: LLMClient,
        observability_factory: Callable[[str, Span], ObservabilityContext],
        recorder_factory: Callable[[ObservabilityContext], TransactionRecorder],
        streaming_orchestrator: StreamingOrchestrator | None = None,
    ):
        self.policy = policy
        self.llm_client = llm_client
        self.observability_factory = observability_factory
        self.recorder_factory = recorder_factory
        self.streaming_orchestrator = streaming_orchestrator or StreamingOrchestrator()

    async def process_request(
        self, request: RequestMessage, transaction_id: str, span: Span
    ) -> RequestMessage:
        """Apply policy to request, record original + final."""
        observability = self.observability_factory(transaction_id, span)
        recorder = self.recorder_factory(observability)

        context = PolicyContext(call_id=transaction_id, span=span, request=request)
        final_request = await self.policy.on_request(request, context)
        await recorder.record_request(request, final_request)

        return final_request

    async def process_streaming_response(
        self, request: RequestMessage, transaction_id: str, span: Span
    ) -> AsyncIterator[ModelResponse]:
        """Process streaming response through policy."""
        observability = self.observability_factory(transaction_id, span)
        recorder = self.recorder_factory(observability)

        llm_stream = self.llm_client.stream(request)
        egress_queue: asyncio.Queue[ModelResponse] = asyncio.Queue()

        ctx = StreamingResponseContext(
            transaction_id=transaction_id,
            final_request=request,
            ingress_assembler=None,
            egress_queue=egress_queue,
            scratchpad={},
            observability=observability,
        )

        feed_complete = asyncio.Event()

        async def policy_processor(
            incoming_queue: asyncio.Queue,
            outgoing_queue: asyncio.Queue,
            keepalive: Callable[[], None],
        ):
            """Process chunks through policy."""
            DELTA_HOOKS = {
                ContentStreamBlock: self.policy.on_content_delta,
                ToolCallStreamBlock: self.policy.on_tool_call_delta,
            }
            COMPLETE_HOOKS = {
                ContentStreamBlock: self.policy.on_content_complete,
                ToolCallStreamBlock: self.policy.on_tool_call_complete,
            }

            async def policy_callback(
                chunk: ModelResponse, state: StreamState, context
            ):
                """Called by assembler on each chunk."""
                keepalive()
                recorder.add_ingress_chunk(chunk)
                await self.policy.on_chunk_received(ctx)

                if state.current_block:
                    block_type = type(state.current_block)
                    if hook := DELTA_HOOKS.get(block_type):
                        await hook(ctx)

                if state.just_completed:
                    block_type = type(state.just_completed)
                    if hook := COMPLETE_HOOKS.get(block_type):
                        await hook(ctx)

                if state.finish_reason:
                    await self.policy.on_finish_reason(ctx)

            ingress_assembler = StreamingChunkAssembler(
                on_chunk_callback=policy_callback
            )
            ctx.ingress_assembler = ingress_assembler

            async def feed_assembler():
                """Feed incoming chunks to assembler."""

                async def queue_to_iter():
                    while True:
                        try:
                            chunk = await incoming_queue.get()
                            if chunk is None:
                                break
                            yield chunk
                        except asyncio.QueueShutDown:
                            break

                try:
                    await ingress_assembler.process(queue_to_iter(), ctx)
                    await self.policy.on_stream_complete(ctx)
                finally:
                    feed_complete.set()

            async def drain_egress():
                """Drain egress queue and forward to outgoing."""
                while True:
                    try:
                        chunk = await asyncio.wait_for(egress_queue.get(), timeout=0.1)
                        recorder.add_egress_chunk(chunk)
                        await outgoing_queue.put(chunk)
                        keepalive()
                    except asyncio.TimeoutError:
                        if feed_complete.is_set():
                            while not egress_queue.empty():
                                try:
                                    chunk = egress_queue.get_nowait()
                                    recorder.add_egress_chunk(chunk)
                                    await outgoing_queue.put(chunk)
                                    keepalive()
                                except asyncio.QueueEmpty:
                                    break
                            break

                await outgoing_queue.put(None)
                outgoing_queue.shutdown()

            await asyncio.gather(feed_assembler(), drain_egress())

        try:
            async for chunk in self.streaming_orchestrator.process(
                llm_stream, policy_processor, timeout_seconds=30.0, span=span
            ):
                yield chunk
        finally:
            await recorder.finalize_streaming()

    async def process_full_response(
        self, request: RequestMessage, transaction_id: str, span: Span
    ) -> ModelResponse:
        """Process non-streaming response through policy."""
        observability = self.observability_factory(transaction_id, span)
        recorder = self.recorder_factory(observability)

        original_response = await self.llm_client.complete(request)

        context = PolicyContext(call_id=transaction_id, span=span, request=request)
        final_response = await self.policy.process_full_response(
            original_response, context
        )

        await recorder.finalize_non_streaming(original_response, final_response)

        return final_response
```

**Testing Strategy:**

Break into separate test files:

1. `test_policy_orchestrator_request.py`:
   - Test `process_request` with mock policy
   - Verify recorder called
   - Verify policy.on_request called

2. `test_policy_orchestrator_streaming.py`:
   - Test `process_streaming_response` with mock LLM stream
   - Verify all policy hooks called in correct order
   - Verify feed_complete timing
   - Verify drain_egress flushes after feed_complete
   - Verify recorder buffers chunks correctly

3. `test_policy_orchestrator_non_streaming.py`:
   - Test `process_full_response` with mock LLM
   - Verify policy.process_full_response called
   - Verify recorder.finalize_non_streaming called

**Acceptance:**
- [ ] `process_request` works correctly
- [ ] `process_streaming_response` calls all hooks in order
- [ ] feed_complete signal works
- [ ] drain_egress flushes after feed_complete
- [ ] `process_full_response` works correctly
- [ ] Block dispatch mapping works
- [ ] All tests pass with 100% coverage

#### 5.2 Factory Function

**File:** `src/luthien_proxy/v2/orchestration/factory.py`

```python
from opentelemetry.trace import Span
from luthien_proxy.v2.policies.policy import Policy
from luthien_proxy.v2.llm.client import LLMClient
from luthien_proxy.v2.observability.context import (
    ObservabilityContext,
    DefaultObservabilityContext,
)
from luthien_proxy.v2.observability.transaction_recorder import (
    TransactionRecorder,
    DefaultTransactionRecorder,
)
from luthien_proxy.v2.orchestration.policy_orchestrator import PolicyOrchestrator


def create_default_orchestrator(
    policy: Policy,
    llm_client: LLMClient,
    db_pool: "DatabasePool | None" = None,
    event_publisher: "RedisEventPublisher | None" = None,
) -> PolicyOrchestrator:
    """Create orchestrator with default dependencies."""

    def observability_factory(transaction_id: str, span: Span) -> ObservabilityContext:
        return DefaultObservabilityContext(
            transaction_id=transaction_id,
            span=span,
            db_pool=db_pool,
            event_publisher=event_publisher,
        )

    def recorder_factory(observability: ObservabilityContext) -> TransactionRecorder:
        return DefaultTransactionRecorder(observability=observability)

    return PolicyOrchestrator(
        policy=policy,
        llm_client=llm_client,
        observability_factory=observability_factory,
        recorder_factory=recorder_factory,
        streaming_orchestrator=None,
    )
```

**Testing:**
- Unit test: Factory creates orchestrator correctly
- Unit test: Orchestrator has correct dependencies

**Acceptance:**
- [ ] Factory creates working orchestrator
- [ ] All dependencies wired correctly
- [ ] Tests pass

### Phase 6: Integration and E2E Tests

#### 6.1 E2E Test Strategy

Create comprehensive e2e tests that validate:
- Streaming works with OpenAI and Anthropic
- Non-streaming works with OpenAI and Anthropic
- Tool calls work with OpenAI and Anthropic
- Policies can transform content
- Observability events are emitted

**Test Files:**

1. `test_streaming_openai.py`
2. `test_streaming_anthropic.py`
3. `test_non_streaming_openai.py`
4. `test_non_streaming_anthropic.py`
5. `test_tool_calls_openai.py`
6. `test_tool_calls_anthropic.py`

**Each test should:**
- Use real LiteLLM calls (with actual API keys in CI)
- Use a simple test policy (e.g., uppercase or passthrough)
- Verify response is correct
- Verify observability events were emitted
- Verify recording worked

**Example Test Structure:**

```python
import pytest
from luthien_proxy.v2.policies.simple_policy import SimplePolicy
from luthien_proxy.v2.llm.litellm_client import LiteLLMClient
from luthien_proxy.v2.orchestration.factory import create_default_orchestrator
from luthien_proxy.v2.types import RequestMessage


class UppercasePolicy(SimplePolicy):
    async def on_response_content(self, content: str, request) -> str:
        return content.upper()


@pytest.mark.e2e
async def test_streaming_openai_with_uppercase_policy():
    """E2E test: OpenAI streaming with uppercase transformation."""
    policy = UppercasePolicy()
    llm_client = LiteLLMClient()

    orchestrator = create_default_orchestrator(
        policy=policy,
        llm_client=llm_client,
        db_pool=None,  # Or real DB for full e2e
        event_publisher=None,
    )

    request = RequestMessage(
        model="gpt-4",
        messages=[{"role": "user", "content": "Say hello"}],
    )

    # Process request
    with tracer.start_as_current_span("test_span") as span:
        final_request = await orchestrator.process_request(
            request, "test-txn-id", span
        )

        # Process streaming response
        chunks = []
        async for chunk in orchestrator.process_streaming_response(
            final_request, "test-txn-id", span
        ):
            chunks.append(chunk)

    # Verify response is uppercase
    content = "".join(
        c.choices[0].delta.get("content", "") for c in chunks if c.choices
    )
    assert content.isupper()
    assert "HELLO" in content or "HI" in content


@pytest.mark.e2e
async def test_tool_calls_openai():
    """E2E test: OpenAI tool calls work correctly."""
    # Similar structure but with tool call request
    # Verify tool calls are preserved
```

**Acceptance:**
- [ ] All e2e tests pass with OpenAI
- [ ] All e2e tests pass with Anthropic
- [ ] Streaming works correctly
- [ ] Non-streaming works correctly
- [ ] Tool calls work correctly
- [ ] Policies can transform content
- [ ] Observability events emitted

---

## Testing Philosophy

### Unit Tests

**Rules:**
1. Test one thing per test
2. Use mocks for all dependencies
3. No async sleep - use proper synchronization
4. Assert specific values, not just "not None"
5. Test error cases explicitly
6. 100% coverage required

**Example:**
```python
async def test_default_transaction_recorder_records_request():
    """Recorder emits event with correct data."""
    observability = Mock(spec=ObservabilityContext)
    recorder = DefaultTransactionRecorder(observability)

    original = RequestMessage(model="gpt-4", messages=[])
    final = RequestMessage(model="gpt-4-turbo", messages=[])

    await recorder.record_request(original, final)

    # Assert specific call
    observability.emit_event.assert_called_once()
    call_args = observability.emit_event.call_args
    assert call_args[1]["event_type"] == "transaction.request_recorded"
    assert call_args[1]["data"]["original_model"] == "gpt-4"
    assert call_args[1]["data"]["final_model"] == "gpt-4-turbo"
```

### E2E Tests

**Rules:**
1. Use real backends (OpenAI, Anthropic)
2. Mark with `@pytest.mark.e2e`
3. Test full flow end-to-end
4. Verify actual behavior, not mocks
5. Test both success and failure paths

### Fail-Fast Guidelines

**Do:**
- Raise `ValueError` for invalid input immediately
- Raise `RuntimeError` for violated invariants
- Use type hints everywhere
- Assert preconditions at function entry
- Use property guards (like `ingress_state`)

**Don't:**
- Check `if x is None: x = default` - require valid input
- Catch broad exceptions and continue
- Provide default values for missing required data
- Silently ignore errors

**Example:**
```python
async def send_text(ctx: StreamingResponseContext, text: str) -> None:
    """Send text chunk to egress."""
    # Fail fast - require non-empty text
    if not text:
        raise ValueError("text must be non-empty")

    chunk = create_text_chunk(text)
    await ctx.egress_queue.put(chunk)
```

---

## Implementation Checklist

### Phase 1: Core Abstractions
- [ ] ObservabilityContext (ABC + implementations)
- [ ] TransactionRecorder (ABC + implementations)
- [ ] Unit tests pass (100% coverage)

### Phase 2: Update Existing Components
- [ ] StreamState (add fields)
- [ ] StreamingChunkAssembler (store chunks)
- [ ] StreamingResponseContext (add observability)
- [ ] Helper functions
- [ ] Unit tests pass

### Phase 3: Policy Abstractions
- [ ] Policy (base interface)
- [ ] SimplePolicy (convenience implementation)
- [ ] Unit tests pass (100% coverage)

### Phase 4: LLM Client
- [ ] LLMClient (ABC)
- [ ] LiteLLMClient (implementation)
- [ ] Unit tests pass

### Phase 5: PolicyOrchestrator
- [ ] PolicyOrchestrator implementation
- [ ] Factory function
- [ ] Unit tests pass (100% coverage for all three methods)

### Phase 6: Integration
- [ ] E2E tests (streaming OpenAI)
- [ ] E2E tests (streaming Anthropic)
- [ ] E2E tests (non-streaming OpenAI)
- [ ] E2E tests (non-streaming Anthropic)
- [ ] E2E tests (tool calls OpenAI)
- [ ] E2E tests (tool calls Anthropic)
- [ ] All tests pass

### Phase 7: Gateway Integration
- [ ] Update gateway_routes.py to use PolicyOrchestrator
- [ ] Verify existing routes still work
- [ ] All integration tests pass

---

## Success Criteria

- [ ] All unit tests pass with 100% coverage
- [ ] All e2e tests pass with real backends
- [ ] No defensive coding (fail fast on invalid input)
- [ ] All functions < 20 lines
- [ ] Precise type hints (no `Any`)
- [ ] Clear, legible code
- [ ] SOLID principles followed
- [ ] SimplePolicy makes policies trivial (~5 lines)
- [ ] ObservabilityContext available everywhere
- [ ] No coupling to DB/Redis in policies

---

**Ready for implementation!**
