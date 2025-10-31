# Pipeline Refactor Specification v4 (FINAL)

**Date:** 2025-10-28
**Status:** Ready for Implementation
**Goal:** Refactor pipeline with clear separation of concerns, matching actual component APIs

---

## Critical Issues Fixed in v4

**v3 had two critical bugs:**

1. ❌ `drain_egress` exits when `finish_reason` is set, losing chunks from `on_stream_complete`
2. ❌ Non-streaming uses `reconstruct_full_response_from_chunks` which expects deltas, not full responses

**v4 fixes:**

1. ✅ `drain_egress` waits for producer (feed_assembler) to finish, then flushes egress_queue
2. ✅ Non-streaming emits ModelResponse directly (no reconstruction needed)
3. ✅ `queue_to_iter` catches `asyncio.QueueShutDown` to handle clean stream termination
4. ✅ `drain_egress` calls `outgoing_queue.shutdown()` so orchestrator's drain loop exits
5. ✅ `StreamingResponseContext.ingress_assembler` is optional to allow instantiation before wiring

---

## Table of Contents

1. [Overview](#overview)
2. [Required Component Changes](#required-component-changes)
3. [Component Specifications](#component-specifications)
4. [Critical Flow Details](#critical-flow-details)
5. [Implementation Plan](#implementation-plan)

---

## Overview

### Design Principles

1. **Reuse proven components:** Keep `StreamingOrchestrator` and `StreamingChunkAssembler`
2. **Minimal changes:** Extend existing components, don't replace
3. **Match actual APIs:** Work with real method signatures
4. **Correct termination:** Producer signals completion, consumer flushes remaining data

### Proposed Solution

**Component Changes Needed:**

1. **StreamState** - Add `raw_chunks: list[ModelResponse]` field
2. **StreamingChunkAssembler** - Store chunks in `state.raw_chunks` during processing
3. **TransactionRecord** - Handle streaming vs non-streaming differently

**New Components:**

1. **LLMClient** - Abstract LLM backend
2. **PolicyOrchestrator** - Thin coordinator using existing StreamingOrchestrator
3. **StreamingResponseContext** - Context for policy with assembler references
4. **Helpers** - send_text, send_chunk, etc.

---

## Required Component Changes

### Change 1: Add raw_chunks to StreamState

**File:** `src/luthien_proxy/v2/streaming/stream_state.py`

```python
@dataclass
class StreamState:
    blocks: list[StreamBlock] = field(default_factory=list)
    current_block: StreamBlock | None = None
    just_completed: StreamBlock | None = None
    finish_reason: str | None = None
    raw_chunks: list[ModelResponse] = field(default_factory=list)  # NEW
    last_emission_index: int = 0  # NEW - tracks where passthrough left off
```

**Purpose:**

- `raw_chunks`: Store original chunks for passthrough (when content unchanged)
- `last_emission_index`: Track which chunks have been emitted to avoid duplicates

### Change 2: Store chunks in StreamingChunkAssembler

**File:** `src/luthien_proxy/v2/streaming/streaming_chunk_assembler.py`

In `process` method, add one line:

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

---

## Component Specifications

### 1. ObservabilityContext

**Purpose:** Unified interface for all observability operations (events, metrics, tracing)

```python
from abc import ABC, abstractmethod
from typing import Any
from opentelemetry.trace import Span

class ObservabilityContext(ABC):
    """
    Abstract interface for observability operations.

    Automatically includes transaction context (call_id, span) in all emissions.
    Policies and components use this for all observability needs without
    coupling to specific backends (DB, Redis, OTel, etc).
    """

    @property
    @abstractmethod
    def transaction_id(self) -> str:
        """Get the transaction ID."""
        pass

    @abstractmethod
    async def emit_event(
        self,
        event_type: str,
        data: dict[str, Any],
    ):
        """
        Emit an event with automatic context enrichment.

        Implementation automatically includes:
        - call_id
        - span context (trace_id, span_id)
        - timestamp
        - Any other contextual data

        Events are emitted to all configured backends (DB, Redis, etc).
        """
        pass

    @abstractmethod
    def record_metric(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ):
        """
        Record a metric with automatic context labels.

        Automatically includes call_id in labels.
        """
        pass

    @abstractmethod
    def add_span_attribute(self, key: str, value: Any):
        """Add attribute to current span."""
        pass

    @abstractmethod
    def add_span_event(self, name: str, attributes: dict[str, Any] | None = None):
        """Add event to current span."""
        pass


class DefaultObservabilityContext(ObservabilityContext):
    """Default implementation using OTel + DB + Redis."""

    def __init__(
        self,
        transaction_id: str,
        span: Span,
        db_pool: DatabasePool | None = None,
        event_publisher: RedisEventPublisher | None = None,
    ):
        self._transaction_id = transaction_id
        self.span = span
        self.db_pool = db_pool
        self.event_publisher = event_publisher

    @property
    def transaction_id(self) -> str:
        return self._transaction_id

    async def emit_event(self, event_type: str, data: dict[str, Any]):
        """Emit to DB, Redis, and OTel span."""
        import time

        # Enrich with automatic context
        enriched_data = {
            "call_id": self._transaction_id,
            "timestamp": time.time(),
            "trace_id": format(self.span.get_span_context().trace_id, '032x'),
            "span_id": format(self.span.get_span_context().span_id, '016x'),
            **data,
        }

        # Emit to DB
        if self.db_pool:
            # Use existing emission helpers
            from luthien_proxy.v2.storage.events import emit_custom_event
            await emit_custom_event(
                call_id=self._transaction_id,
                event_type=event_type,
                data=enriched_data,
                db_pool=self.db_pool,
            )

        # Emit to Redis
        if self.event_publisher:
            await self.event_publisher.publish_event(
                call_id=self._transaction_id,
                event_type=event_type,
                data=data,
            )

        # Add to OTel span
        self.add_span_event(event_type, data)

    def record_metric(self, name: str, value: float, labels: dict[str, str] | None = None):
        """Record metric with automatic labels."""
        from opentelemetry import metrics

        all_labels = {
            "call_id": self._transaction_id,
            **(labels or {}),
        }

        meter = metrics.get_meter(__name__)
        counter = meter.create_counter(name)
        counter.add(value, all_labels)

    def add_span_attribute(self, key: str, value: Any):
        """Add attribute to current span."""
        self.span.set_attribute(key, value)

    def add_span_event(self, name: str, attributes: dict[str, Any] | None = None):
        """Add event to current span."""
        self.span.add_event(name, attributes or {})


class NoOpObservabilityContext(ObservabilityContext):
    """No-op implementation for testing."""

    def __init__(self, transaction_id: str):
        self._transaction_id = transaction_id

    @property
    def transaction_id(self) -> str:
        return self._transaction_id

    async def emit_event(self, event_type: str, data: dict[str, Any]):
        pass

    def record_metric(self, name: str, value: float, labels: dict[str, str] | None = None):
        pass

    def add_span_attribute(self, key: str, value: Any):
        pass

    def add_span_event(self, name: str, attributes: dict[str, Any] | None = None):
        pass
```

**Key Design:**

- **Single interface**: One place for all observability operations
- **Automatic context**: Call ID, trace ID, span ID included automatically
- **Backend agnostic**: Policies don't know about DB/Redis/OTel
- **Easy testing**: `NoOpObservabilityContext` for clean tests
- **Extensible**: Add new backends without API changes

---

### 2. TransactionRecorder (Simplified Interface)

**Purpose:** Abstract interface for transaction recording

```python
class TransactionRecorder(ABC):
    """Abstract interface for recording transactions."""

    @abstractmethod
    async def record_request(
        self,
        original: RequestMessage,
        final: RequestMessage,
    ):
        """Record original and final request."""
        pass

    @abstractmethod
    def add_ingress_chunk(self, chunk: ModelResponse):
        """Buffer ingress chunk (streaming only)."""
        pass

    @abstractmethod
    def add_egress_chunk(self, chunk: ModelResponse):
        """Buffer egress chunk (streaming only)."""
        pass

    @abstractmethod
    async def finalize_streaming(self):
        """Finalize streaming response recording."""
        pass

    @abstractmethod
    async def finalize_non_streaming(
        self,
        original_response: ModelResponse,
        final_response: ModelResponse,
    ):
        """Finalize non-streaming response recording."""
        pass


class NoOpTransactionRecorder(TransactionRecorder):
    """No-op recorder for testing."""

    async def record_request(self, original, final):
        pass

    def add_ingress_chunk(self, chunk):
        pass

    def add_egress_chunk(self, chunk):
        pass

    async def finalize_streaming(self):
        pass

    async def finalize_non_streaming(self, original_response, final_response):
        pass


class DefaultTransactionRecorder(TransactionRecorder):
    """Default implementation using ObservabilityContext."""

    def __init__(
        self,
        observability: ObservabilityContext,
    ):
        self.observability = observability
        # For streaming: buffer chunks
        self.ingress_chunks: list[ModelResponse] = []
        self.egress_chunks: list[ModelResponse] = []

    async def record_request(
        self,
        original: RequestMessage,
        final: RequestMessage,
    ):
        """Record original and final request via observability context."""
        await self.observability.emit_event(
            event_type="transaction.request_recorded",
            data={
                "original_model": original.model,
                "final_model": final.model,
                "original_request": original.model_dump(exclude_none=True),
                "final_request": final.model_dump(exclude_none=True),
            },
        )

        # Add span attributes
        self.observability.add_span_attribute("request.model", final.model)
        self.observability.add_span_attribute("request.message_count", len(final.messages))

    def add_ingress_chunk(self, chunk: ModelResponse):
        """Buffer ingress chunk (streaming only)."""
        self.ingress_chunks.append(chunk)

    def add_egress_chunk(self, chunk: ModelResponse):
        """Buffer egress chunk (streaming only)."""
        self.egress_chunks.append(chunk)

    async def finalize_streaming(self):
        """
        Finalize streaming response recording.

        Reconstructs full responses from buffered chunks and emits.
        """
        from luthien_proxy.v2.storage.events import reconstruct_full_response_from_chunks

        # Reconstruct returns dict
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

        # Record metrics
        self.observability.record_metric("response.chunks.ingress", len(self.ingress_chunks))
        self.observability.record_metric("response.chunks.egress", len(self.egress_chunks))

    async def finalize_non_streaming(
        self,
        original_response: ModelResponse,
        final_response: ModelResponse,
    ):
        """
        Finalize non-streaming response recording.

        Emits full ModelResponse objects directly (no reconstruction).
        """
        await self.observability.emit_event(
            event_type="transaction.non_streaming_response_recorded",
            data={
                "original_finish_reason": self._get_finish_reason(original_response),
                "final_finish_reason": self._get_finish_reason(final_response),
                "original_response": original_response.model_dump(),
                "final_response": final_response.model_dump(),
            },
        )

        # Add span attributes
        finish_reason = self._get_finish_reason(final_response)
        if finish_reason:
            self.observability.add_span_attribute("response.finish_reason", finish_reason)

    def _get_finish_reason(self, response: ModelResponse) -> str | None:
        """Extract finish_reason from response."""
        choices = response.model_dump().get("choices", [])
        return choices[0].get("finish_reason") if choices else None
```

**Key Design:**

- **Uses ObservabilityContext**: Single dependency instead of db_pool + event_publisher
- **Automatic context enrichment**: Call ID, trace ID added automatically
- **Span attributes**: Adds relevant attributes for tracing
- **Metrics**: Records chunk counts and other metrics
- **Testing**: Easy to test with `NoOpObservabilityContext`

---

### 3. StreamingResponseContext

**Purpose:** Context for policy methods during streaming

```python
from dataclasses import dataclass
from typing import Any
from luthien_proxy.v2.streaming.streaming_chunk_assembler import StreamingChunkAssembler
from luthien_proxy.v2.streaming.stream_state import StreamState

@dataclass
class StreamingResponseContext:
    """
    Context for policy invocations during streaming.

    Policy reads from ingress_assembler.state and writes to egress_queue.
    Observability operations via ctx.observability.

    Note: ingress_assembler is set after context creation but before
    any policy callbacks are invoked.
    """

    transaction_id: str
    final_request: RequestMessage
    ingress_assembler: StreamingChunkAssembler | None
    egress_queue: asyncio.Queue[ModelResponse]
    scratchpad: dict[str, Any]
    observability: ObservabilityContext

    @property
    def ingress_state(self) -> StreamState:
        """Current ingress state (blocks, raw_chunks, finish_reason)."""
        if self.ingress_assembler is None:
            raise RuntimeError("ingress_assembler not yet initialized")
        return self.ingress_assembler.state
```

**Usage in policies:**

```python
class ContentFilterPolicy(SimplePolicy):
    async def on_response_content(self, content: str, request: RequestMessage) -> str:
        if self._is_sensitive(content):
            # Emit event with automatic context
            await ctx.observability.emit_event(
                event_type="policy.content_blocked",
                data={"reason": "sensitive_content", "content_length": len(content)},
            )
            raise PolicyViolation("Content contains sensitive information")
        return content
```

---

### 4. Helper Functions

**Purpose:** Simplify policy operations

```python
from luthien_proxy.v2.policies.utils import create_text_chunk
from litellm.types.utils import ModelResponse

async def send_text(ctx: StreamingResponseContext, text: str):
    """Helper to send text chunk to egress."""
    chunk = create_text_chunk(text)
    await ctx.egress_queue.put(chunk)

async def send_chunk(ctx: StreamingResponseContext, chunk: ModelResponse):
    """Helper to send chunk to egress."""
    await ctx.egress_queue.put(chunk)

def get_last_ingress_chunk(ctx: StreamingResponseContext) -> ModelResponse | None:
    """Get most recent chunk from ingress."""
    chunks = ctx.ingress_state.raw_chunks
    return chunks[-1] if chunks else None

async def passthrough_last_chunk(ctx: StreamingResponseContext):
    """Passthrough most recent ingress chunk to egress."""
    chunk = get_last_ingress_chunk(ctx)
    if chunk:
        await send_chunk(ctx, chunk)
```

---

### 5. LLMClient

**Purpose:** Abstract interface for LLM backend communication

```python
from abc import ABC, abstractmethod
from typing import AsyncIterator
from litellm.types.utils import ModelResponse

class LLMClient(ABC):
    """Abstract interface for LLM backend communication."""

    @abstractmethod
    async def stream(self, request: RequestMessage) -> AsyncIterator[ModelResponse]:
        """Stream response from LLM backend (OpenAI format)."""
        pass

    @abstractmethod
    async def complete(self, request: RequestMessage) -> ModelResponse:
        """Get complete response from LLM backend (OpenAI format)."""
        pass

class LiteLLMClient(LLMClient):
    """LLM client using litellm library."""

    async def stream(self, request: RequestMessage) -> AsyncIterator[ModelResponse]:
        data = request.model_dump(exclude_none=True)
        data["stream"] = True
        response = await litellm.acompletion(**data)
        async for chunk in response:
            yield chunk

    async def complete(self, request: RequestMessage) -> ModelResponse:
        data = request.model_dump(exclude_none=True)
        data["stream"] = False
        response = await litellm.acompletion(**data)
        return cast(ModelResponse, response)
```

---

### 6. PolicyOrchestrator

**Purpose:** Coordinate flow using existing StreamingOrchestrator

```python
import asyncio
from typing import AsyncIterator, Callable
from opentelemetry import trace
from luthien_proxy.v2.streaming.streaming_orchestrator import StreamingOrchestrator
from luthien_proxy.v2.streaming.streaming_chunk_assembler import StreamingChunkAssembler
from luthien_proxy.v2.streaming.stream_blocks import ContentStreamBlock, ToolCallStreamBlock

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
        """
        Initialize orchestrator with dependencies.

        Args:
            policy: Policy to apply to requests/responses
            llm_client: Client for LLM backend communication
            observability_factory: Factory (transaction_id, span) -> ObservabilityContext
            recorder_factory: Factory (observability) -> TransactionRecorder
            streaming_orchestrator: Optional orchestrator (defaults to StreamingOrchestrator)
        """
        self.policy = policy
        self.llm_client = llm_client
        self.observability_factory = observability_factory
        self.recorder_factory = recorder_factory
        self.streaming_orchestrator = streaming_orchestrator or StreamingOrchestrator()

    async def process_request(
        self,
        request: RequestMessage,
        transaction_id: str,
        span: Span,
    ) -> RequestMessage:
        """Apply policy to request, record original + final."""
        # Create observability context
        observability = self.observability_factory(transaction_id, span)

        # Create recorder with observability
        recorder = self.recorder_factory(observability)

        context = PolicyContext(
            call_id=transaction_id,
            span=span,
            request=request,
        )

        final_request = await self.policy.on_request(request, context)
        await recorder.record_request(request, final_request)

        return final_request

    async def process_streaming_response(
        self,
        request: RequestMessage,
        transaction_id: str,
        span: Span,
    ) -> AsyncIterator[ModelResponse]:
        """
        Process streaming response through policy.

        Correct termination: feed_assembler completes → policy.on_stream_complete →
        drain_egress flushes remaining chunks.
        """
        # Create observability context
        observability = self.observability_factory(transaction_id, span)

        # Create recorder with observability
        recorder = self.recorder_factory(observability)

        # Get LLM stream
        llm_stream = self.llm_client.stream(request)

        # Create egress queue
        egress_queue: asyncio.Queue[ModelResponse] = asyncio.Queue()

        # Create context
        ctx = StreamingResponseContext(
            transaction_id=transaction_id,
            final_request=request,
            ingress_assembler=None,  # Set in policy_processor
            egress_queue=egress_queue,
            scratchpad={},
            observability=observability,
        )

        # Signal for drain_egress to know when feed_assembler is done
        feed_complete = asyncio.Event()

        # Define policy processor
        async def policy_processor(
            incoming_queue: asyncio.Queue,
            outgoing_queue: asyncio.Queue,
            keepalive: Callable[[], None],
        ):
            """
            Process chunks through policy.

            CRITICAL: drain_egress waits for feed_complete signal,
            then flushes remaining egress_queue items.
            """
            # Block type -> policy hook mapping
            DELTA_HOOKS = {
                ContentStreamBlock: self.policy.on_content_delta,
                ToolCallStreamBlock: self.policy.on_tool_call_delta,
            }
            COMPLETE_HOOKS = {
                ContentStreamBlock: self.policy.on_content_complete,
                ToolCallStreamBlock: self.policy.on_tool_call_complete,
            }

            # Policy callback for assembler
            async def policy_callback(chunk: ModelResponse, state: StreamState, context: Any):
                """Called by assembler on each chunk."""
                keepalive()

                # Buffer for recording
                recorder.add_ingress_chunk(chunk)

                # Call policy hooks
                await self.policy.on_chunk_received(ctx)

                # Delta hook
                if state.current_block:
                    block_type = type(state.current_block)
                    if hook := DELTA_HOOKS.get(block_type):
                        await hook(ctx)

                # Complete hook
                if state.just_completed:
                    block_type = type(state.just_completed)
                    if hook := COMPLETE_HOOKS.get(block_type):
                        await hook(ctx)

                # Finish reason hook
                if state.finish_reason:
                    await self.policy.on_finish_reason(ctx)

            # Create ingress assembler
            ingress_assembler = StreamingChunkAssembler(on_chunk_callback=policy_callback)
            ctx.ingress_assembler = ingress_assembler

            # Feed assembler task
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
                            # Orchestrator closed the queue (stream ended)
                            break

                try:
                    await ingress_assembler.process(queue_to_iter(), ctx)
                    # Stream complete - call policy hook
                    await self.policy.on_stream_complete(ctx)
                finally:
                    # Signal that we're done producing
                    feed_complete.set()

            # Drain egress task
            async def drain_egress():
                """
                Drain egress queue and forward to outgoing.

                CRITICAL: Wait for feed_complete signal, then flush remaining chunks.
                This ensures chunks from on_stream_complete are not lost.
                """
                while True:
                    try:
                        # Use short timeout to check feed_complete periodically
                        chunk = await asyncio.wait_for(egress_queue.get(), timeout=0.1)
                        recorder.add_egress_chunk(chunk)
                        await outgoing_queue.put(chunk)
                        keepalive()
                    except asyncio.TimeoutError:
                        # Check if producer is done
                        if feed_complete.is_set():
                            # Producer done - flush remaining chunks
                            while not egress_queue.empty():
                                try:
                                    chunk = egress_queue.get_nowait()
                                    recorder.add_egress_chunk(chunk)
                                    await outgoing_queue.put(chunk)
                                    keepalive()
                                except asyncio.QueueEmpty:
                                    break
                            # All chunks flushed
                            break

                # Signal outgoing complete
                await outgoing_queue.put(None)
                # CRITICAL: Shutdown queue so orchestrator's drain loop breaks
                outgoing_queue.shutdown()

            # Run both tasks
            await asyncio.gather(feed_assembler(), drain_egress())

        # Use StreamingOrchestrator
        try:
            async for chunk in self.streaming_orchestrator.process(
                llm_stream,
                policy_processor,
                timeout_seconds=30.0,
                span=span,
            ):
                yield chunk
        finally:
            # Finalize recording (streaming path)
            await recorder.finalize_streaming()

    async def process_full_response(
        self,
        request: RequestMessage,
        transaction_id: str,
        span: Span,
    ) -> ModelResponse:
        """
        Process non-streaming response through policy.

        CRITICAL: Store full ModelResponse directly (no reconstruction).
        """
        # Create observability context
        observability = self.observability_factory(transaction_id, span)

        # Create recorder with observability
        recorder = self.recorder_factory(observability)

        # Call LLM
        original_response = await self.llm_client.complete(request)

        # Create policy context
        context = PolicyContext(
            call_id=transaction_id,
            span=span,
            request=request,
        )

        # Apply policy
        final_response = await self.policy.process_full_response(original_response, context)

        # Finalize (non-streaming path) - pass responses directly
        await recorder.finalize_non_streaming(original_response, final_response)

        return final_response
```

**Key Design:**

- **ObservabilityContext:** Single interface for all observability operations
- **Compositional factories:** Observability created first, then recorder
- **Block dispatch mapping:** New block types register hooks without editing orchestrator
- **feed_complete signal:** Producer sets this when done, consumer waits for it
- **Flush after signal:** drain_egress empties egress_queue after feed_complete
- **No finish_reason dependency:** Works even if LLM doesn't send finish_reason
- **Non-streaming:** Passes full responses to recorder directly

### Factory Function

```python
def create_default_orchestrator(
    policy: Policy,
    llm_client: LLMClient,
    db_pool: DatabasePool | None = None,
    event_publisher: RedisEventPublisher | None = None,
) -> PolicyOrchestrator:
    """
    Create orchestrator with default dependencies.

    Use this in production code. Tests can construct PolicyOrchestrator
    directly with mock factories.
    """
    # Observability factory creates context with automatic enrichment
    def observability_factory(transaction_id: str, span: Span) -> ObservabilityContext:
        return DefaultObservabilityContext(
            transaction_id=transaction_id,
            span=span,
            db_pool=db_pool,
            event_publisher=event_publisher,
        )

    # Recorder factory receives observability context
    def recorder_factory(observability: ObservabilityContext) -> TransactionRecorder:
        return DefaultTransactionRecorder(observability=observability)

    return PolicyOrchestrator(
        policy=policy,
        llm_client=llm_client,
        observability_factory=observability_factory,
        recorder_factory=recorder_factory,
        streaming_orchestrator=None,  # Use default
    )
```

**Testing Example:**

```python
# In tests, inject no-op observability
def test_streaming_policy():
    orchestrator = PolicyOrchestrator(
        policy=MyTestPolicy(),
        llm_client=MockLLMClient(),
        observability_factory=lambda tid, span: NoOpObservabilityContext(tid),
        recorder_factory=lambda obs: NoOpTransactionRecorder(),
    )
    # Test without DB/Redis/OTel dependencies
```

---

### 7. Policy Interface

#### Base Policy Class

**Purpose:** Low-level streaming control for advanced use cases

```python
class Policy(ABC):
    """
    Base policy class with full streaming control.

    Use this for advanced policies that need real-time chunk manipulation.
    Most policies should inherit from SimplePolicy instead.
    """

    async def on_request(
        self,
        request: RequestMessage,
        context: PolicyContext,
    ) -> RequestMessage:
        """Process request before sending to LLM."""
        return request

    async def on_chunk_received(self, ctx: StreamingResponseContext):
        """Called on every chunk."""
        pass

    async def on_content_delta(self, ctx: StreamingResponseContext):
        """Called when content delta received."""
        pass

    async def on_content_complete(self, ctx: StreamingResponseContext):
        """Called when content block completes."""
        pass

    async def on_tool_call_delta(self, ctx: StreamingResponseContext):
        """Called when tool call delta received."""
        pass

    async def on_tool_call_complete(self, ctx: StreamingResponseContext):
        """Called when tool call block completes."""
        pass

    async def on_finish_reason(self, ctx: StreamingResponseContext):
        """Called when finish_reason received."""
        pass

    async def on_stream_complete(self, ctx: StreamingResponseContext):
        """
        Called when stream completes.

        CRITICAL: Any chunks emitted here will be flushed by drain_egress.
        """
        pass

    async def process_full_response(
        self,
        response: ModelResponse,
        context: PolicyContext,
    ) -> ModelResponse:
        """Process non-streaming response."""
        return response
```

#### SimplePolicy Class

**Purpose:** High-level content transformation for common use cases (95% of policies)

```python
class SimplePolicy(Policy):
    """
    Convenience base class that handles streaming complexity internally.

    Subclasses implement simple content-level methods. Streaming is handled
    automatically by buffering content and applying transformations when complete.

    Use this for:
    - Content transformations (uppercase, redaction, etc.)
    - Content validation (profanity filters, PII detection)
    - Request modifications (prompt injection, model selection)

    For real-time streaming control (e.g., token-by-token redaction),
    inherit from Policy directly instead.
    """

    # ===== Simple methods that subclasses override =====

    async def on_request_simple(
        self,
        request: RequestMessage,
    ) -> RequestMessage:
        """
        Transform/validate request before LLM.

        Raise PolicyViolation to reject the request.
        """
        return request

    async def on_response_content(
        self,
        content: str,
        request: RequestMessage,
    ) -> str:
        """
        Transform complete response content.

        Called once per content block after all chunks are assembled.
        Raise PolicyViolation to reject the response.
        """
        return content

    async def on_response_tool_call(
        self,
        tool_call: ToolCall,
        request: RequestMessage,
    ) -> ToolCall:
        """
        Transform/validate a complete tool call.

        Called once per tool call after all deltas are assembled.
        Raise PolicyViolation to reject the response.
        """
        return tool_call

    # ===== Implementation of streaming hooks =====

    async def on_request(
        self,
        request: RequestMessage,
        context: PolicyContext,
    ) -> RequestMessage:
        """Delegate to simple method."""
        return await self.on_request_simple(request)

    async def on_content_complete(
        self,
        ctx: StreamingResponseContext,
        content: str,
    ):
        """
        Called when content block is complete. Transform and emit.

        If content is transformed, emits as new chunks.
        If content is unchanged, passes through original chunks to preserve timing.
        """
        transformed = await self.on_response_content(content, ctx.final_request)

        if transformed != content:
            # Content changed - emit as single chunk
            await send_text(ctx, transformed)
        else:
            # Content unchanged - preserve original chunking
            await passthrough_accumulated_chunks(ctx)

    async def on_tool_call_complete(
        self,
        ctx: StreamingResponseContext,
        tool_call: ToolCall,
    ):
        """
        Called when tool call is complete. Transform and emit.

        If tool call is transformed, emits as new chunks.
        If tool call is unchanged, passes through original chunks.
        """
        transformed = await self.on_response_tool_call(tool_call, ctx.final_request)

        if transformed != tool_call:
            # Tool call changed - emit modified version
            await send_tool_call(ctx, transformed)
        else:
            # Tool call unchanged - preserve original chunks
            await passthrough_accumulated_chunks(ctx)

    async def on_content_delta(self, ctx: StreamingResponseContext):
        """
        Buffer content deltas, don't emit yet.

        SimplePolicy waits for complete content before transforming.
        Assembler handles buffering automatically.
        """
        pass  # Assembler buffers, we process in on_content_complete

    async def on_tool_call_delta(self, ctx: StreamingResponseContext):
        """
        Buffer tool call deltas, don't emit yet.

        SimplePolicy waits for complete tool call before transforming.
        """
        pass  # Assembler buffers, we process in on_tool_call_complete

    async def on_chunk_received(self, ctx: StreamingResponseContext):
        """
        Pass through metadata chunks immediately.

        Chunks without content/tool deltas (e.g., usage stats) are forwarded.
        """
        chunk = get_last_ingress_chunk(ctx)
        if chunk and not self._has_content_or_tool_delta(chunk):
            await send_chunk(ctx, chunk)

    def _has_content_or_tool_delta(self, chunk: ModelResponse) -> bool:
        """Check if chunk contains content or tool call delta."""
        if not chunk.choices:
            return False
        delta = chunk.choices[0].delta
        if not delta:
            return False
        return bool(delta.get("content") or delta.get("tool_calls"))
```

#### Helper Function Updates

```python
async def passthrough_accumulated_chunks(ctx: StreamingResponseContext):
    """
    Emit all chunks buffered since last emission.

    Preserves original chunk timing and structure when content is unchanged.
    """
    # Get chunks that assembled into current block
    start_idx = ctx.ingress_state.last_emission_index
    chunks = ctx.ingress_state.raw_chunks[start_idx:]

    for chunk in chunks:
        await send_chunk(ctx, chunk)

    # Update marker
    ctx.ingress_state.last_emission_index = len(ctx.ingress_state.raw_chunks)

async def send_tool_call(ctx: StreamingResponseContext, tool_call: ToolCall):
    """Helper to send complete tool call as chunk."""
    chunk = create_tool_call_chunk(tool_call)
    await ctx.egress_queue.put(chunk)
```

#### Example Policies

**Simple Uppercase Policy:**

```python
class UppercasePolicy(SimplePolicy):
    """Transform all response content to uppercase."""

    async def on_response_content(self, content: str, request: RequestMessage) -> str:
        return content.upper()
```

**Simple Content Filter:**

```python
class ProfanityFilterPolicy(SimplePolicy):
    """Block responses containing prohibited words."""

    def __init__(self):
        self.bad_words = {"badword1", "badword2"}

    async def on_response_content(
        self,
        content: str,
        request: RequestMessage
    ) -> str:
        for word in self.bad_words:
            if word in content.lower():
                raise PolicyViolation(
                    message=f"Response contains prohibited word: {word}",
                    severity="high",
                )
        return content
```

**Simple Request Modifier:**

```python
class SystemPromptInjectionPolicy(SimplePolicy):
    """Add safety prompt to all requests."""

    async def on_request_simple(self, request: RequestMessage) -> RequestMessage:
        # Add safety prompt at the beginning
        safety_msg = {
            "role": "system",
            "content": "You are a helpful, harmless, and honest assistant."
        }
        request.messages.insert(0, safety_msg)
        return request
```

**Advanced Real-Time Streaming (uses Policy directly):**

```python
class RealTimeRedactionPolicy(Policy):
    """
    Redact sensitive patterns in real-time as chunks arrive.

    Inherits from Policy (not SimplePolicy) for token-by-token control.
    """

    def __init__(self):
        self.ssn_pattern = re.compile(r'\d{3}-\d{2}-\d{4}')

    async def on_content_delta(self, ctx: StreamingResponseContext):
        """Redact SSNs in real-time."""
        chunk = get_last_ingress_chunk(ctx)
        if not chunk or not chunk.choices:
            return

        delta = chunk.choices[0].delta
        content = delta.get("content", "")

        if content:
            redacted = self.ssn_pattern.sub("XXX-XX-XXXX", content)
            if redacted != content:
                # Send redacted version
                await send_text(ctx, redacted)
            else:
                # Pass through unchanged
                await send_chunk(ctx, chunk)
```

---

## Critical Flow Details

### Streaming Termination (CRITICAL)

**The Problem:**
- v3 checked `finish_reason` to exit drain_egress
- This loses chunks from `on_stream_complete`
- Fails if LLM doesn't send finish_reason

**The Solution:**

```python
# feed_assembler signals when done
feed_complete = asyncio.Event()

async def feed_assembler():
    await ingress_assembler.process(...)
    await policy.on_stream_complete(ctx)  # Policy may emit chunks here
    feed_complete.set()  # Signal we're done

async def drain_egress():
    while True:
        try:
            chunk = await asyncio.wait_for(egress_queue.get(), timeout=0.1)
            # Forward chunk
        except asyncio.TimeoutError:
            if feed_complete.is_set():
                # Producer done - flush remaining
                while not egress_queue.empty():
                    chunk = egress_queue.get_nowait()
                    # Forward chunk
                break  # Exit after flush
```

**Guarantees:**
- ✅ Chunks from `on_stream_complete` are flushed
- ✅ Works without finish_reason
- ✅ No race conditions
- ✅ Clean termination

### Non-Streaming Recording (CRITICAL)

**The Problem:**
- v3 used `reconstruct_full_response_from_chunks` for non-streaming
- That helper expects deltas (streaming), not full responses
- Result: Lost message content and metadata

**The Solution:**

```python
# Non-streaming path
original_response = await llm_client.complete(request)  # Full ModelResponse
final_response = await policy.process_full_response(original_response, context)

# Store directly (no reconstruction)
record.original_response = original_response
record.final_response = final_response

# Emit full responses
await record.finalize_non_streaming()
    # Calls: emit_response_event(
    #     original_response=original_response.model_dump(),
    #     final_response=final_response.model_dump(),
    # )
```

**Guarantees:**
- ✅ Full message content preserved
- ✅ All metadata preserved (finish_reason, usage, etc.)
- ✅ No data loss
- ✅ Correct reconstruction path per response type

### Queue Termination (CRITICAL)

#### Problem 1: QueueShutDown not handled

- StreamingOrchestrator calls `queue.shutdown()` when source stream ends
- `queue_to_iter` must catch `asyncio.QueueShutDown` or it raises and cancels pipeline
- Without this, the pipeline crashes instead of terminating cleanly

**Solution:**

```python
async def queue_to_iter():
    while True:
        try:
            chunk = await incoming_queue.get()
            if chunk is None:
                break
            yield chunk
        except asyncio.QueueShutDown:
            # Orchestrator closed the queue (stream ended)
            break
```

#### Problem 2: Outgoing queue never shut down

- `drain_egress` enqueues `None` sentinel to signal completion
- But StreamingOrchestrator's drain loop relies on `queue.shutdown()` to exit
- Without shutdown, orchestrator blocks forever after reading `None`

**Solution:**

```python
async def drain_egress():
    # ... drain loop ...

    # Signal outgoing complete
    await outgoing_queue.put(None)
    # CRITICAL: Shutdown queue so orchestrator's drain loop breaks
    outgoing_queue.shutdown()
```

**Guarantees:**

- ✅ Clean termination when upstream closes queue
- ✅ No pipeline crashes from unhandled QueueShutDown
- ✅ Orchestrator's drain loop exits properly
- ✅ No blocking/hanging on completion

### Type Safety: Optional ingress_assembler (CRITICAL)

**Problem:**

- `StreamingResponseContext` is created before the `ingress_assembler` exists
- Original spec declared `ingress_assembler: StreamingChunkAssembler` (non-optional)
- But orchestrator instantiates with `ingress_assembler=None` and sets it later
- Pyright flags this as a type error: `None` incompatible with `StreamingChunkAssembler`

**Solution:**

```python
@dataclass
class StreamingResponseContext:
    transaction_id: str
    final_request: RequestMessage
    ingress_assembler: StreamingChunkAssembler | None  # Allow None during construction
    egress_queue: asyncio.Queue[ModelResponse]
    scratchpad: dict[str, Any]
    span: Span

    @property
    def ingress_state(self) -> StreamState:
        """Current ingress state (blocks, raw_chunks, finish_reason)."""
        if self.ingress_assembler is None:
            raise RuntimeError("ingress_assembler not yet initialized")
        return self.ingress_assembler.state
```

**Flow:**

1. Context created with `ingress_assembler=None`
2. Inside `policy_processor`, assembler is created and assigned: `ctx.ingress_assembler = ingress_assembler`
3. Before any policy callbacks, assembler is guaranteed to be set
4. Property guard ensures runtime safety if accessed too early

**Guarantees:**

- ✅ Passes Pyright type checking
- ✅ Runtime guard prevents access before initialization
- ✅ Policy callbacks always see initialized assembler
- ✅ No type: ignore needed

---

## Implementation Plan

### Phase 1: Extend Components (Week 1)

**Task 1.1: Add raw_chunks to StreamState**

**File:** `src/luthien_proxy/v2/streaming/stream_state.py`

**Change:** Add field `raw_chunks: list[ModelResponse] = field(default_factory=list)`

**Acceptance:** [ ] Field added, tests pass

**Task 1.2: Store chunks in assembler**

**File:** `src/luthien_proxy/v2/streaming/streaming_chunk_assembler.py`

**Change:** Add `self.state.raw_chunks.append(chunk)` in `process`

**Acceptance:** [ ] Chunks stored, tests pass

### Phase 2: New Components (Week 2)

**Task 2.1: LLMClient**
- NEW: `src/luthien_proxy/v2/llm/client.py`
- NEW: `src/luthien_proxy/v2/llm/litellm_client.py`
- **Acceptance:** [ ] Interface + impl, tests pass

**Task 2.2: Context + Helpers**
- NEW: `src/luthien_proxy/v2/streaming/streaming_response_context.py`
- NEW: `src/luthien_proxy/v2/streaming/helpers.py`
- **Acceptance:** [ ] Context + helpers, tests pass

**Task 2.3: TransactionRecord**
- NEW: `src/luthien_proxy/v2/transaction_record.py`
- **Acceptance:** [ ] Streaming + non-streaming paths, tests pass

### Phase 3: PolicyOrchestrator (Week 3)

**Task 3.1: Request + Non-Streaming**
- NEW: `src/luthien_proxy/v2/orchestrator.py`
- **Acceptance:** [ ] process_request + process_full_response work

**Task 3.2: Streaming**
- MODIFY: `src/luthien_proxy/v2/orchestrator.py`
- **Acceptance:** [ ] process_streaming_response with correct termination

### Phase 4: Gateway Integration (Week 4)

**Task 4.1: OpenAI Endpoint**
- MODIFY: `src/luthien_proxy/v2/gateway_routes.py`
- **Acceptance:** [ ] Uses orchestrator

**Task 4.2: Anthropic Endpoint**
- MODIFY: `src/luthien_proxy/v2/gateway_routes.py`
- **Acceptance:** [ ] Uses orchestrator

### Phase 5: Policy Migration (Week 5)

**Task 5.1: Update EventBasedPolicy**
- MODIFY: `src/luthien_proxy/v2/policies/event_based_policy.py`
- **Acceptance:** [ ] New context

**Task 5.2: Update Policies**
- MODIFY: All policies
- **Acceptance:** [ ] All work with new context

---

## Success Criteria

- [ ] All existing tests pass
- [ ] New components >90% coverage
- [ ] OpenAI + Anthropic work
- [ ] Streaming + non-streaming work
- [ ] No lost chunks from on_stream_complete
- [ ] Non-streaming preserves full response data
- [ ] Observability events correct
- [ ] No API mismatches

---

## Testing Focus Areas

### Critical Tests

1. **Streaming termination:**
   - Policy emits chunks in `on_stream_complete` → chunks reach client
   - LLM drops stream without finish_reason → clean exit
   - Network error mid-stream → proper cleanup

2. **Non-streaming recording:**
   - Full response content preserved
   - finish_reason preserved
   - Usage metadata preserved

3. **Egress flush:**
   - Multiple chunks in egress_queue when feed_complete → all flushed
   - Empty egress_queue when feed_complete → clean exit

---

## SOLID Design Improvements

This spec addresses key architectural concerns:

### 1. Single Responsibility Principle

**ObservabilityContext abstraction:**

- Single interface for all observability operations (events, metrics, tracing)
- Separates observability from business logic
- Automatic context enrichment (call_id, trace_id, timestamps)

**TransactionRecorder abstraction:**

- Recording logic separated from orchestration
- `NoOpTransactionRecorder` for testing without observability
- `DefaultTransactionRecorder` uses ObservabilityContext

**Block dispatch mapping:**

- Block type → hook mapping centralizes dispatch logic
- New block types register without editing orchestrator body

### 2. Open/Closed Principle

**Extension points:**

- New observability backends: Implement `ObservabilityContext` interface
- New block types: Add to `DELTA_HOOKS` / `COMPLETE_HOOKS` dictionaries
- New recorder strategies: Implement `TransactionRecorder` interface
- New LLM backends: Implement `LLMClient` interface
- New policies: Inherit from `Policy` or `SimplePolicy`

### 3. Dependency Inversion Principle

**Abstractions:**

- `ObservabilityContext` interface (policies don't know about DB/Redis/OTel)
- `TransactionRecorder` interface (not concrete class)
- Compositional factories (observability → recorder)
- `LLMClient` interface for backend abstraction
- Optional `StreamingOrchestrator` injection for testing

**Testing benefits:**

```python
# Easy to test without DB/Redis/OTel
orchestrator = PolicyOrchestrator(
    policy=test_policy,
    llm_client=mock_llm,
    observability_factory=lambda tid, span: NoOpObservabilityContext(tid),
    recorder_factory=lambda obs: NoOpTransactionRecorder(),
)
```

### 4. Interface Segregation Principle

**SimplePolicy abstraction:**

- 95% of policies use simple content-level interface
- Don't need to know about chunks, queues, or assemblers
- 5% that need streaming control use full `Policy` interface

**ObservabilityContext methods:**

- Focused interface with clear methods
- Policies only use what they need (emit_event, record_metric, etc.)
- Not forced to implement unused methods

### 5. Liskov Substitution Principle

**Polymorphic interfaces:**

- Any `ObservabilityContext` implementation works
- Any `TransactionRecorder` implementation works
- Any `LLMClient` implementation works
- Any `Policy` implementation works
- `SimplePolicy` is substitutable for `Policy`

---

**End of Specification v4 (FINAL)**
