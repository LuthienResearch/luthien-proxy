# Pipeline Refactor Specification v3

**Date:** 2025-10-28
**Status:** Ready for Implementation
**Goal:** Refactor pipeline with clear separation of concerns, matching actual component APIs

---

## Critical Issue Summary

**v2 spec had API mismatches with existing code:**

1. ❌ StreamingChunkAssembler requires callback + async iterator (not single chunk API)
2. ❌ StreamState doesn't have `raw_chunks` field
3. ❌ `reconstruct_full_response_from_chunks` returns `dict` not `ModelResponse`
4. ❌ `passthrough_last_chunk` missing `async def`

**v3 fixes all these by:**

1. ✅ Using StreamingChunkAssembler's actual API (callback-based)
2. ✅ Adding `raw_chunks` to StreamState (small extension)
3. ✅ Wrapping dict in ModelResponse or changing TransactionRecord
4. ✅ Making helper properly async

---

## Table of Contents

1. [Overview](#overview)
2. [Required Component Changes](#required-component-changes)
3. [Component Specifications](#component-specifications)
4. [Data Flow](#data-flow)
5. [Implementation Plan](#implementation-plan)

---

## Overview

### Design Principles (Unchanged)

1. **Reuse proven components:** Keep `StreamingOrchestrator` and `StreamingChunkAssembler`
2. **Minimal changes:** Extend existing components, don't replace
3. **Match actual APIs:** Work with real method signatures

### Proposed Solution

**Component Changes Needed:**

1. **StreamState** - Add `raw_chunks: list[ModelResponse]` field
2. **StreamingChunkAssembler** - Store chunks in `state.raw_chunks` during `_update_state`
3. **TransactionRecord** - Handle `dict` from `reconstruct_full_response_from_chunks`

**New Components:**

1. **LLMClient** - Abstract LLM backend
2. **PolicyOrchestrator** - Thin coordinator using existing StreamingOrchestrator
3. **StreamingResponseContext** - Context for policy with assembler references

---

## Required Component Changes

### Change 1: Add raw_chunks to StreamState

**File:** `src/luthien_proxy/v2/streaming/stream_state.py`

**Current:**
```python
@dataclass
class StreamState:
    blocks: list[StreamBlock] = field(default_factory=list)
    current_block: StreamBlock | None = None
    just_completed: StreamBlock | None = None
    finish_reason: str | None = None
```

**Add:**
```python
@dataclass
class StreamState:
    blocks: list[StreamBlock] = field(default_factory=list)
    current_block: StreamBlock | None = None
    just_completed: StreamBlock | None = None
    finish_reason: str | None = None
    raw_chunks: list[ModelResponse] = field(default_factory=list)  # NEW
```

**Rationale:**
- Policy needs access to raw chunks for debugging/logging
- Enables TransactionRecord to buffer chunks
- Small change, no breaking changes to existing code

### Change 2: Store raw chunks in StreamingChunkAssembler

**File:** `src/luthien_proxy/v2/streaming/streaming_chunk_assembler.py`

**In `process` method, add:**
```python
async def process(
    self,
    incoming: AsyncIterator[ModelResponse],
    context: Any,
) -> None:
    async for chunk in incoming:
        # NEW: Store raw chunk
        self.state.raw_chunks.append(chunk)

        # Existing code
        self._update_state(chunk)
        chunk = self._strip_empty_content(chunk)
        await self.on_chunk(chunk, self.state, context)
        self.state.just_completed = None
```

**Rationale:**
- Minimal change (one line)
- Doesn't break existing policies
- Provides data needed for recording

---

## Component Specifications

### 1. TransactionRecord

**Purpose:** Record and emit transaction data

```python
class TransactionRecord:
    """Records original vs modified requests/responses."""

    def __init__(
        self,
        transaction_id: str,
        db_pool: DatabasePool | None,
        event_publisher: RedisEventPublisher | None,
    ):
        self.transaction_id = transaction_id
        self.db_pool = db_pool
        self.event_publisher = event_publisher

        # Data
        self.original_request: RequestMessage | None = None
        self.final_request: RequestMessage | None = None
        self.ingress_chunks: list[ModelResponse] = []
        self.egress_chunks: list[ModelResponse] = []

    async def record_request(
        self,
        original: RequestMessage,
        final: RequestMessage,
    ):
        """Record original and final request, emit events."""
        self.original_request = original
        self.final_request = final

        emit_request_event(
            call_id=self.transaction_id,
            original_request=original.model_dump(exclude_none=True),
            final_request=final.model_dump(exclude_none=True),
            db_pool=self.db_pool,
            redis_conn=None,
        )

        if self.event_publisher:
            await self.event_publisher.publish_event(
                call_id=self.transaction_id,
                event_type="transaction.request_recorded",
                data={
                    "original_model": original.model,
                    "final_model": final.model,
                },
            )

    def add_ingress_chunk(self, chunk: ModelResponse):
        """Buffer ingress chunk for later recording."""
        self.ingress_chunks.append(chunk)

    def add_egress_chunk(self, chunk: ModelResponse):
        """Buffer egress chunk for later recording."""
        self.egress_chunks.append(chunk)

    async def finalize(self):
        """
        Finalize recording - reconstruct responses from chunks and emit.

        Called after streaming completes.
        """
        from luthien_proxy.v2.storage.events import reconstruct_full_response_from_chunks

        # Reconstruct returns dict, not ModelResponse
        original_response_dict = reconstruct_full_response_from_chunks(self.ingress_chunks)
        final_response_dict = reconstruct_full_response_from_chunks(self.egress_chunks)

        # Emit to DB - emit_response_event expects dicts
        emit_response_event(
            call_id=self.transaction_id,
            original_response=original_response_dict,
            final_response=final_response_dict,
            db_pool=self.db_pool,
            redis_conn=None,
        )

        if self.event_publisher:
            await self.event_publisher.publish_event(
                call_id=self.transaction_id,
                event_type="transaction.response_recorded",
                data={
                    "ingress_chunks": len(self.ingress_chunks),
                    "egress_chunks": len(self.egress_chunks),
                },
            )
```

**Key Design:**
- Buffers chunks during streaming
- Calls `finalize()` after streaming completes
- Works with `dict` from `reconstruct_full_response_from_chunks`
- Simple buffer + emit pattern

---

### 2. StreamingResponseContext

**Purpose:** Context for policy methods

```python
from dataclasses import dataclass
from typing import Any
from opentelemetry.trace import Span
from luthien_proxy.v2.streaming.streaming_chunk_assembler import StreamingChunkAssembler
from luthien_proxy.v2.streaming.stream_state import StreamState

@dataclass
class StreamingResponseContext:
    """
    Context for policy invocations during streaming.

    Contains references to ingress/egress assemblers.
    Policy reads ingress state and can trigger egress operations.
    """

    transaction_id: str
    final_request: RequestMessage
    ingress_assembler: StreamingChunkAssembler  # Can read state
    egress_queue: asyncio.Queue[ModelResponse]  # Policy writes here
    scratchpad: dict[str, Any]
    span: Span

    # Convenience properties
    @property
    def ingress_state(self) -> StreamState:
        """Current ingress state."""
        return self.ingress_assembler.state
```

**Key Design:**
- Policy **reads** from `ingress_assembler.state`
- Policy **writes** to `egress_queue.put(chunk)`
- No direct egress assembler access (egress processes its own queue)

---

### 3. Helper Functions

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

async def passthrough_last_chunk(ctx: StreamingResponseContext):  # Fixed: async def
    """Passthrough most recent ingress chunk to egress."""
    chunk = get_last_ingress_chunk(ctx)
    if chunk:
        await send_chunk(ctx, chunk)
```

**Key Design:**
- All async helpers properly declared with `async def`
- Helpers write to `ctx.egress_queue`
- Simple, functional style

---

### 4. LLMClient

**(Same as v2 - no changes)**

```python
from abc import ABC, abstractmethod

class LLMClient(ABC):
    @abstractmethod
    async def stream(self, request: RequestMessage) -> AsyncIterator[ModelResponse]:
        pass

    @abstractmethod
    async def complete(self, request: RequestMessage) -> ModelResponse:
        pass

class LiteLLMClient(LLMClient):
    async def stream(self, request: RequestMessage):
        data = request.model_dump(exclude_none=True)
        data["stream"] = True
        response = await litellm.acompletion(**data)
        async for chunk in response:
            yield chunk

    async def complete(self, request: RequestMessage):
        data = request.model_dump(exclude_none=True)
        data["stream"] = False
        response = await litellm.acompletion(**data)
        return cast(ModelResponse, response)
```

---

### 5. PolicyOrchestrator

**Purpose:** Coordinate flow using existing StreamingOrchestrator

```python
from opentelemetry import trace
from luthien_proxy.v2.streaming.streaming_orchestrator import StreamingOrchestrator
from luthien_proxy.v2.streaming.streaming_chunk_assembler import StreamingChunkAssembler

tracer = trace.get_tracer(__name__)

class PolicyOrchestrator:
    """Orchestrates request/response flow through policy layer."""

    def __init__(
        self,
        policy: LuthienPolicy,
        llm_client: LLMClient,
        db_pool: DatabasePool | None = None,
        event_publisher: RedisEventPublisher | None = None,
    ):
        self.policy = policy
        self.llm_client = llm_client
        self.db_pool = db_pool
        self.event_publisher = event_publisher
        self.streaming_orchestrator = StreamingOrchestrator()

    async def process_request(
        self,
        request: RequestMessage,
        transaction_id: str,
        span: Span,
    ) -> RequestMessage:
        """Apply policy to request, record original + final."""
        record = TransactionRecord(
            transaction_id=transaction_id,
            db_pool=self.db_pool,
            event_publisher=self.event_publisher,
        )

        context = PolicyContext(
            call_id=transaction_id,
            span=span,
            request=request,
        )

        final_request = await self.policy.process_request(request, context)
        await record.record_request(request, final_request)

        return final_request

    async def process_streaming_response(
        self,
        request: RequestMessage,
        transaction_id: str,
        span: Span,
    ) -> AsyncIterator[ModelResponse]:
        """
        Process streaming response through policy.

        Uses existing StreamingOrchestrator + StreamingChunkAssembler.
        Works with actual component APIs.
        """
        record = TransactionRecord(
            transaction_id=transaction_id,
            db_pool=self.db_pool,
            event_publisher=self.event_publisher,
        )

        # Get LLM stream
        llm_stream = self.llm_client.stream(request)

        # Create egress queue (policy writes here)
        egress_queue: asyncio.Queue[ModelResponse] = asyncio.Queue()

        # Create context for policy
        ctx = StreamingResponseContext(
            transaction_id=transaction_id,
            final_request=request,
            ingress_assembler=None,  # Will be set in policy_processor
            egress_queue=egress_queue,
            scratchpad={},
            span=span,
        )

        # Define policy processor for StreamingOrchestrator
        async def policy_processor(
            incoming_queue: asyncio.Queue,
            outgoing_queue: asyncio.Queue,
            keepalive: Callable[[], None],
        ):
            """
            Process chunks through policy using StreamingChunkAssembler.

            - Creates ingress assembler with policy callback
            - Assembler processes incoming chunks
            - Policy callback gets invoked with (chunk, state, context)
            - Policy writes to egress_queue
            - We drain egress_queue and forward to outgoing_queue
            """
            # Create ingress assembler with policy callback
            async def policy_callback(chunk: ModelResponse, state: StreamState, context: Any):
                """Called by assembler on each chunk."""
                keepalive()

                # Buffer for recording
                record.add_ingress_chunk(chunk)

                # Call policy hook
                await self.policy.on_chunk_received(ctx)

                # Call specific hooks based on state
                if state.current_block:
                    if isinstance(state.current_block, ContentStreamBlock):
                        await self.policy.on_content_delta(ctx)
                    elif isinstance(state.current_block, ToolCallStreamBlock):
                        await self.policy.on_tool_call_delta(ctx)

                if state.just_completed:
                    if isinstance(state.just_completed, ContentStreamBlock):
                        await self.policy.on_content_complete(ctx)
                    elif isinstance(state.just_completed, ToolCallStreamBlock):
                        await self.policy.on_tool_call_complete(ctx)

                if state.finish_reason:
                    await self.policy.on_finish_reason(ctx)

            ingress_assembler = StreamingChunkAssembler(on_chunk_callback=policy_callback)
            ctx.ingress_assembler = ingress_assembler  # Now policy can access state

            # Launch tasks
            async def feed_assembler():
                """Feed incoming chunks to assembler."""
                async def queue_to_iter():
                    while True:
                        chunk = await incoming_queue.get()
                        if chunk is None:
                            break
                        yield chunk

                await ingress_assembler.process(queue_to_iter(), ctx)
                await self.policy.on_stream_complete(ctx)

            async def drain_egress():
                """Drain egress queue and forward to outgoing."""
                while True:
                    try:
                        chunk = await asyncio.wait_for(egress_queue.get(), timeout=0.1)
                        record.add_egress_chunk(chunk)
                        await outgoing_queue.put(chunk)
                        keepalive()
                    except asyncio.TimeoutError:
                        # Check if assembler is done
                        if ingress_assembler.state.finish_reason:
                            break

                # Signal complete
                await outgoing_queue.put(None)

            await asyncio.gather(feed_assembler(), drain_egress())

        # Use existing StreamingOrchestrator
        try:
            async for chunk in self.streaming_orchestrator.process(
                llm_stream,
                policy_processor,
                timeout_seconds=30.0,
                span=span,
            ):
                yield chunk
        finally:
            # Finalize recording
            await record.finalize()

    async def process_full_response(
        self,
        request: RequestMessage,
        transaction_id: str,
        span: Span,
    ) -> ModelResponse:
        """Process non-streaming response through policy."""
        record = TransactionRecord(
            transaction_id=transaction_id,
            db_pool=self.db_pool,
            event_publisher=self.event_publisher,
        )

        original_response = await self.llm_client.complete(request)

        context = PolicyContext(
            call_id=transaction_id,
            span=span,
            request=request,
        )

        final_response = await self.policy.process_full_response(original_response, context)

        # For non-streaming, buffer as if they were chunks
        record.add_ingress_chunk(original_response)
        record.add_egress_chunk(final_response)
        await record.finalize()

        return final_response
```

**Key Design:**
- Uses **actual StreamingChunkAssembler API** (callback + async iterator)
- Assembler calls policy callback on each chunk
- Policy writes to `egress_queue`
- Separate task drains egress and forwards to outgoing
- Matches real component signatures

---

### 6. Policy Interface

**Updated EventBasedPolicy:**

```python
class EventBasedPolicy:
    """Base class for event-driven policies."""

    async def on_request(
        self,
        request: RequestMessage,
        context: PolicyContext,
    ) -> RequestMessage:
        """Process request before sending to LLM."""
        return request

    async def on_chunk_received(self, ctx: StreamingResponseContext):
        """Called on every chunk. Policy can access ctx.ingress_state."""
        pass

    async def on_content_delta(self, ctx: StreamingResponseContext):
        """
        Called when content delta received.

        Default: passthrough.
        """
        await passthrough_last_chunk(ctx)

    async def on_content_complete(self, ctx: StreamingResponseContext):
        """Called when content block completes."""
        pass

    async def on_tool_call_delta(self, ctx: StreamingResponseContext):
        """
        Called when tool call delta received.

        Default: passthrough.
        """
        await passthrough_last_chunk(ctx)

    async def on_tool_call_complete(self, ctx: StreamingResponseContext):
        """Called when tool call block completes."""
        pass

    async def on_finish_reason(self, ctx: StreamingResponseContext):
        """Called when finish_reason received."""
        pass

    async def on_stream_complete(self, ctx: StreamingResponseContext):
        """Called when stream completes."""
        pass

    async def process_full_response(
        self,
        response: ModelResponse,
        context: PolicyContext,
    ) -> ModelResponse:
        """Process non-streaming response."""
        return response
```

**Example Policy:**

```python
class UppercasePolicy(EventBasedPolicy):
    """Uppercase all content."""

    async def on_content_delta(self, ctx):
        # Read from ingress
        if ctx.ingress_state.current_block:
            text = ctx.ingress_state.current_block.content
            upper = text.upper()

            # Write to egress
            await send_text(ctx, upper)
```

---

## Data Flow

### Streaming Flow

```
LLM stream
  ↓
StreamingOrchestrator.process(llm_stream, policy_processor, ...)
  ├─ incoming_queue ← LLM chunks
  │
  ├─ policy_processor:
  │   ├─ StreamingChunkAssembler(on_chunk_callback=policy_callback)
  │   │   ├─ Processes incoming chunks
  │   │   ├─ Updates state (blocks, raw_chunks)
  │   │   └─ Calls policy_callback(chunk, state, ctx)
  │   │
  │   ├─ policy_callback:
  │   │   ├─ Buffer chunk in TransactionRecord
  │   │   ├─ Call policy hooks based on state
  │   │   └─ Policy writes to egress_queue
  │   │
  │   └─ drain_egress task:
  │       ├─ Read from egress_queue
  │       ├─ Buffer in TransactionRecord
  │       └─ Forward to outgoing_queue
  │
  └─ outgoing_queue → yield chunks
  ↓
Gateway
```

---

## Implementation Plan

### Phase 1: Extend Existing Components (Week 1)

#### Task 1.1: Add raw_chunks to StreamState

**File:** `src/luthien_proxy/v2/streaming/stream_state.py`

**Change:**
```python
@dataclass
class StreamState:
    blocks: list[StreamBlock] = field(default_factory=list)
    current_block: StreamBlock | None = None
    just_completed: StreamBlock | None = None
    finish_reason: str | None = None
    raw_chunks: list[ModelResponse] = field(default_factory=list)  # ADD
```

**Tests:**
- Verify field exists
- Check default factory

**Acceptance:** [ ] Field added, tests pass

#### Task 1.2: Store chunks in StreamingChunkAssembler

**File:** `src/luthien_proxy/v2/streaming/streaming_chunk_assembler.py`

**Change:** Add `self.state.raw_chunks.append(chunk)` in `process` method

**Tests:**
- Feed chunks, verify stored in state

**Acceptance:** [ ] Chunks stored, tests pass

### Phase 2: New Components (Week 2)

#### Task 2.1: LLMClient

**Files:**
- NEW: `src/luthien_proxy/v2/llm/client.py`
- NEW: `src/luthien_proxy/v2/llm/litellm_client.py`

**Acceptance:** [ ] Interface + implementation, tests pass

#### Task 2.2: StreamingResponseContext + Helpers

**Files:**
- NEW: `src/luthien_proxy/v2/streaming/streaming_response_context.py`
- NEW: `src/luthien_proxy/v2/streaming/helpers.py`

**Acceptance:** [ ] Context + helpers, tests pass

#### Task 2.3: TransactionRecord

**Files:**
- NEW: `src/luthien_proxy/v2/transaction_record.py`

**Acceptance:** [ ] Records + emits, tests pass

### Phase 3: PolicyOrchestrator (Week 3)

#### Task 3.1: Request + Non-Streaming

**File:** NEW: `src/luthien_proxy/v2/orchestrator.py`

**Acceptance:** [ ] process_request and process_full_response work

#### Task 3.2: Streaming

**File:** MODIFY: `src/luthien_proxy/v2/orchestrator.py`

**Acceptance:** [ ] process_streaming_response works with assembler API

### Phase 4: Gateway Integration (Week 4)

#### Task 4.1: OpenAI Endpoint

**File:** MODIFY: `src/luthien_proxy/v2/gateway_routes.py`

**Acceptance:** [ ] OpenAI endpoint uses orchestrator

#### Task 4.2: Anthropic Endpoint

**File:** MODIFY: `src/luthien_proxy/v2/gateway_routes.py`

**Acceptance:** [ ] Anthropic endpoint uses orchestrator

### Phase 5: Policy Migration (Week 5)

#### Task 5.1: Update EventBasedPolicy

**File:** MODIFY: `src/luthien_proxy/v2/policies/event_based_policy.py`

**Acceptance:** [ ] New context, helpers

#### Task 5.2: Update Existing Policies

**Files:** MODIFY: All policies

**Acceptance:** [ ] All policies work with new context

---

## Success Criteria

- [ ] All existing tests pass
- [ ] New components have >90% coverage
- [ ] OpenAI + Anthropic endpoints work
- [ ] Streaming + non-streaming work
- [ ] Observability events emitted
- [ ] No API mismatches

---

**End of Specification v3**
