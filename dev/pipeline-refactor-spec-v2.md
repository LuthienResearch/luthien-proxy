# Pipeline Refactor Specification v2

**Date:** 2025-10-28
**Status:** Ready for Implementation
**Goal:** Refactor request/response pipeline with clear separation of concerns, reusing proven components

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture Principles](#architecture-principles)
3. [Component Specifications](#component-specifications)
4. [Data Flow](#data-flow)
5. [Format Conversion Points](#format-conversion-points)
6. [Implementation Plan](#implementation-plan)
7. [Testing Strategy](#testing-strategy)
8. [Migration Path](#migration-path)

---

## Overview

### Current Problems

1. **Mixed responsibilities:** SynchronousControlPlane handles both policy execution and observability (buffering, event emission)
2. **Scattered format conversion:** Conversion logic spread across gateway and streaming code
3. **Tight coupling:** LLM calls embedded directly in gateway, hard to test/swap
4. **Implicit state:** Request/response tracking via dict lookup instead of explicit passing

### Design Principles

1. **Reuse proven components:** Keep `StreamingOrchestrator` - it already handles timeout, backpressure, task lifecycle correctly
2. **Simplify, don't replace:** Extend what works instead of reimplementing
3. **Direct state access:** Give policies access to current state, not stale snapshots
4. **Clear ownership:** Each component has one clear responsibility

### Proposed Solution

**Keep:**
- `StreamingOrchestrator` - queue coordination, timeout monitoring, task management
- `StreamingChunkAssembler` - parsing chunks into blocks
- `TimeoutTracker` - proven timeout mechanism
- Queue-based architecture - handles backpressure naturally

**Add:**
- `LLMClient` - abstract LLM backend calls
- `TransactionRecord` - consolidated recording logic
- `PolicyOrchestrator` - thin coordinator using existing pieces
- `StreamingResponseContext` - policy context with direct state access

**Remove:**
- Duplicate event queuing in new `StreamingResponse` class
- Stale state snapshots in events
- Reimplementation of timeout/queue logic

---

## Architecture Principles

### 1. Reuse StreamingOrchestrator

`StreamingOrchestrator` already provides:
- ✅ Queue-based coordination (incoming/outgoing queues)
- ✅ Timeout monitoring (properly launches and cancels monitor task)
- ✅ Task lifecycle management (TaskGroup, proper cleanup)
- ✅ Batching optimization (`get_available`)
- ✅ Error handling and propagation
- ✅ OTel tracing hooks

**Don't reimplement this.** Build on top of it.

### 2. Direct State Access, Not Snapshots

**Problem:** Deep copying state on every event is expensive and still creates stale snapshots by the time policy reads them.

**Solution:** Give policies direct access to current state:

```python
@dataclass
class StreamingResponseContext:
    transaction_id: str
    final_request: RequestMessage
    ingress: StreamingChunkAssembler  # Direct reference to assembler
    egress: StreamingChunkAssembler   # Direct reference to assembler
    scratchpad: dict
    span: Span

# Policy reads current state
async def on_content_delta(self, ctx):
    # Always current, never stale
    current_state = ctx.ingress.state
    current_block = current_state.current_block
```

**Benefits:**
- No deep copy overhead
- Always accurate state
- Policy can inspect full history via `state.raw_chunks`
- Policy can check what's in egress too

### 3. Event Dispatch Based on State Transitions

Instead of enqueueing events with state snapshots, **check state transitions and call policy methods directly**:

```python
async def process_chunk(chunk, ctx):
    # Get state before processing
    prev_just_completed = ctx.ingress.state.just_completed

    # Process chunk
    await ctx.ingress.process(chunk)

    # Check state transitions
    state = ctx.ingress.state

    # Call policy based on what changed
    await policy.on_chunk_received(ctx)

    if state.current_block and isinstance(state.current_block, ContentStreamBlock):
        await policy.on_content_delta(ctx)

    if state.just_completed and state.just_completed != prev_just_completed:
        if isinstance(state.just_completed, ContentStreamBlock):
            await policy.on_content_complete(ctx)
        elif isinstance(state.just_completed, ToolCallStreamBlock):
            await policy.on_tool_call_complete(ctx)
```

**Benefits:**
- No event queue overhead
- No stale snapshots
- Simple, direct dispatch
- Policy sees current state

### 4. Policy Pushes Directly to Egress Assembler

**Problem in v1 spec:** Helpers needed egress but policy had no way to get it.

**Solution:** Context has direct reference to egress:

```python
async def on_content_delta(self, ctx):
    # Modify text
    text = ctx.ingress.state.current_block.content.upper()

    # Push to egress
    chunk = create_text_chunk(text)
    await ctx.egress.process(chunk)
```

**Even simpler with helper:**

```python
async def send_text(ctx, text):
    """Helper to create and send text chunk."""
    chunk = create_text_chunk(text)
    await ctx.egress.process(chunk)

# Policy uses:
async def on_content_delta(self, ctx):
    text = ctx.ingress.state.current_block.content.upper()
    await send_text(ctx, text)
```

---

## Component Specifications

### 1. TransactionRecord

**Purpose:** Record and emit transaction data for observability

**Type:** Data + Logic (owned by PolicyOrchestrator)

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
        self.original_response: ModelResponse | None = None
        self.final_response: ModelResponse | None = None

    async def record_request(
        self,
        original: RequestMessage,
        final: RequestMessage,
    ):
        """Record original and final request, emit events."""
        self.original_request = original
        self.final_request = final

        # Emit to DB (queued, non-blocking)
        emit_request_event(
            call_id=self.transaction_id,
            original_request=original.model_dump(exclude_none=True),
            final_request=final.model_dump(exclude_none=True),
            db_pool=self.db_pool,
            redis_conn=None,
        )

        # Publish real-time event
        if self.event_publisher:
            await self.event_publisher.publish_event(
                call_id=self.transaction_id,
                event_type="transaction.request_recorded",
                data={
                    "original_model": original.model,
                    "final_model": final.model,
                },
            )

    async def record_response(
        self,
        original_chunks: list[ModelResponse],
        final_chunks: list[ModelResponse],
    ):
        """
        Record original and final response (reconstructed from chunks), emit events.

        For streaming responses, we reconstruct full responses from buffered chunks.
        """
        from luthien_proxy.v2.storage.events import reconstruct_full_response_from_chunks

        if original_chunks:
            self.original_response = reconstruct_full_response_from_chunks(original_chunks)

        if final_chunks:
            self.final_response = reconstruct_full_response_from_chunks(final_chunks)

        # Emit to DB (queued, non-blocking)
        if self.original_response and self.final_response:
            emit_response_event(
                call_id=self.transaction_id,
                original_response=self.original_response.model_dump(),
                final_response=self.final_response.model_dump(),
                db_pool=self.db_pool,
                redis_conn=None,
            )

            # Publish real-time event
            if self.event_publisher:
                await self.event_publisher.publish_event(
                    call_id=self.transaction_id,
                    event_type="transaction.response_recorded",
                    data={
                        "original_chunks": len(original_chunks),
                        "final_chunks": len(final_chunks),
                    },
                )
```

**Key Design:**
- Handles both streaming (from chunks) and non-streaming (direct response)
- Uses existing `reconstruct_full_response_from_chunks` utility
- Non-blocking emission (queued for background)

---

### 2. StreamingChunkAssembler

**No changes needed.** This is our existing component (renamed from `StreamProcessor`).

**Current capabilities:**
- Parses chunks into `StreamState` (blocks + raw_chunks)
- Detects block boundaries
- Sets `just_completed` when blocks finish
- Stores `finish_reason`

**Location:** `src/luthien_proxy/v2/streaming/streaming_chunk_assembler.py`

---

### 3. StreamingResponseContext

**Purpose:** Context passed to policy methods during streaming

**Type:** Immutable dataclass with direct references

```python
from dataclasses import dataclass
from typing import Any
from opentelemetry.trace import Span

@dataclass
class StreamingResponseContext:
    """
    Context for policy invocations during streaming.

    Provides direct access to ingress/egress assemblers (not snapshots).
    Policy reads current state and pushes to egress.
    """

    transaction_id: str
    final_request: RequestMessage
    ingress: StreamingChunkAssembler  # Direct reference - policy reads current state
    egress: StreamingChunkAssembler   # Direct reference - policy pushes chunks here
    scratchpad: dict[str, Any]        # Policy-specific state
    span: Span                        # OpenTelemetry span

    # Convenience properties
    @property
    def ingress_state(self) -> StreamState:
        """Current ingress state (never stale)."""
        return self.ingress.state

    @property
    def egress_state(self) -> StreamState:
        """Current egress state."""
        return self.egress.state
```

**Key Design:**
- Direct references to assemblers (not state snapshots)
- Properties for convenient access to current state
- Policy can call `ctx.ingress.process()` if needed (advanced use case)
- Policy always sees current state, never stale

---

### 4. Helper Functions

**Purpose:** Simplify common policy operations

```python
from luthien_proxy.v2.policies.utils import create_text_chunk
from litellm.types.utils import ModelResponse

async def send_text(ctx: StreamingResponseContext, text: str):
    """
    Helper to send text chunk to egress.

    Creates a text chunk and processes it through egress assembler.
    """
    chunk = create_text_chunk(text)
    await ctx.egress.process(chunk)

async def send_chunk(ctx: StreamingResponseContext, chunk: ModelResponse):
    """
    Helper to send chunk directly to egress.

    Useful for passthrough policies or when policy has constructed a chunk.
    """
    await ctx.egress.process(chunk)

def get_last_ingress_chunk(ctx: StreamingResponseContext) -> ModelResponse | None:
    """Get the most recent chunk from ingress (convenience)."""
    chunks = ctx.ingress_state.raw_chunks
    return chunks[-1] if chunks else None

def passthrough_last_chunk(ctx: StreamingResponseContext):
    """
    Passthrough the most recent ingress chunk to egress.

    Common pattern for policies that don't modify content.
    """
    chunk = get_last_ingress_chunk(ctx)
    if chunk:
        await send_chunk(ctx, chunk)
```

**Key Design:**
- Helpers take context and operate on ctx.egress
- Simple, functional style
- Can be in `v2/streaming/helpers.py` or `v2/policies/helpers.py`

---

### 5. LLMClient (Abstract)

**Purpose:** Abstract interface for LLM backend calls

```python
from abc import ABC, abstractmethod
from typing import AsyncIterator
from litellm.types.utils import ModelResponse

class LLMClient(ABC):
    """Abstract interface for LLM backend communication."""

    @abstractmethod
    async def stream(
        self,
        request: RequestMessage,
    ) -> AsyncIterator[ModelResponse]:
        """
        Stream response from LLM backend.

        Args:
            request: OpenAI-format request

        Returns:
            AsyncIterator yielding OpenAI-format ModelResponse chunks
        """
        pass

    @abstractmethod
    async def complete(
        self,
        request: RequestMessage,
    ) -> ModelResponse:
        """
        Get complete (non-streaming) response from LLM backend.

        Args:
            request: OpenAI-format request

        Returns:
            OpenAI-format ModelResponse
        """
        pass
```

**Concrete Implementation:**

```python
import litellm
from typing import cast

class LiteLLMClient(LLMClient):
    """LLM client using litellm library."""

    async def stream(
        self,
        request: RequestMessage,
    ) -> AsyncIterator[ModelResponse]:
        """Stream via litellm.acompletion."""
        data = request.model_dump(exclude_none=True)
        data["stream"] = True

        response = await litellm.acompletion(**data)
        async for chunk in response:  # type: ignore[attr-defined]
            yield chunk

    async def complete(
        self,
        request: RequestMessage,
    ) -> ModelResponse:
        """Complete via litellm.acompletion."""
        data = request.model_dump(exclude_none=True)
        data["stream"] = False

        response = await litellm.acompletion(**data)
        return cast(ModelResponse, response)
```

**Key Design:**
- Abstract interface enables testing with mocks
- litellm already returns OpenAI format - no conversion needed
- Simple pass-through to litellm

**Location:**
- Interface: `src/luthien_proxy/v2/llm/client.py`
- Implementation: `src/luthien_proxy/v2/llm/litellm_client.py`

---

### 6. PolicyOrchestrator

**Purpose:** Coordinate flow between components using existing StreamingOrchestrator

**Type:** Thin orchestrator / coordinator

```python
from opentelemetry import trace
from luthien_proxy.v2.streaming.streaming_orchestrator import StreamingOrchestrator

tracer = trace.get_tracer(__name__)

class PolicyOrchestrator:
    """
    Orchestrates request/response flow through policy layer.

    Responsibilities:
    - Own TransactionRecord for observability
    - Apply policy to requests
    - Coordinate streaming using existing StreamingOrchestrator
    - Dispatch policy hooks based on state transitions
    """

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
        """
        Apply policy to request, record original + final.

        Returns:
            Final request (after policy modification)
        """
        # Create transaction record
        record = TransactionRecord(
            transaction_id=transaction_id,
            db_pool=self.db_pool,
            event_publisher=self.event_publisher,
        )

        # Create policy context
        context = PolicyContext(
            call_id=transaction_id,
            span=span,
            request=request,
        )

        # Apply policy
        final_request = await self.policy.process_request(request, context)

        # Record
        await record.record_request(request, final_request)

        return final_request

    async def process_streaming_response(
        self,
        request: RequestMessage,
        transaction_id: str,
        span: Span,
    ) -> AsyncIterator[ModelResponse]:
        """
        Process streaming response through policy using existing StreamingOrchestrator.

        Flow:
        1. Get stream from LLM
        2. Create ingress/egress assemblers
        3. Use StreamingOrchestrator to coordinate:
           - Feed LLM chunks → ingress assembler
           - Dispatch policy hooks based on state transitions
           - Policy pushes to egress assembler
           - Yield from egress

        Args:
            request: Final request to send to LLM
            transaction_id: Transaction ID
            span: OpenTelemetry span

        Yields:
            ModelResponse chunks (OpenAI format)
        """
        # Create transaction record
        record = TransactionRecord(
            transaction_id=transaction_id,
            db_pool=self.db_pool,
            event_publisher=self.event_publisher,
        )

        # Get LLM stream
        llm_stream = self.llm_client.stream(request)

        # Create assemblers
        ingress = StreamingChunkAssembler()
        egress = StreamingChunkAssembler()

        # Create context
        ctx = StreamingResponseContext(
            transaction_id=transaction_id,
            final_request=request,
            ingress=ingress,
            egress=egress,
            scratchpad={},
            span=span,
        )

        # Buffers for recording
        ingress_chunks: list[ModelResponse] = []
        egress_chunks: list[ModelResponse] = []

        # Define processor for StreamingOrchestrator
        async def policy_processor(
            incoming_queue: asyncio.Queue,
            outgoing_queue: asyncio.Queue,
            keepalive: Callable[[], None],
        ):
            """
            Process chunks through policy.

            Reads from incoming_queue (LLM chunks) →
            feeds to ingress assembler →
            dispatches policy hooks →
            policy pushes to egress →
            writes egress chunks to outgoing_queue
            """
            try:
                while True:
                    # Get chunk from incoming queue
                    chunk = await incoming_queue.get()
                    if chunk is None:
                        # Stream complete
                        break

                    keepalive()  # Prevent timeout

                    # Buffer for recording
                    ingress_chunks.append(chunk)

                    # Process through ingress assembler
                    prev_just_completed = ingress.state.just_completed
                    await ingress.process(chunk)

                    # Dispatch policy hooks based on state transitions
                    await self._dispatch_policy_hooks(
                        ctx,
                        prev_just_completed,
                        keepalive,
                    )

                    # Get any chunks policy pushed to egress
                    new_egress_chunks = egress.state.raw_chunks[len(egress_chunks):]
                    for egress_chunk in new_egress_chunks:
                        egress_chunks.append(egress_chunk)
                        await outgoing_queue.put(egress_chunk)
                        keepalive()

                # Call on_stream_complete
                await self.policy.on_stream_complete(ctx)

                # Get any final chunks from egress
                final_egress_chunks = egress.state.raw_chunks[len(egress_chunks):]
                for egress_chunk in final_egress_chunks:
                    egress_chunks.append(egress_chunk)
                    await outgoing_queue.put(egress_chunk)

                # Signal complete
                await outgoing_queue.put(None)

            except Exception as e:
                logger.error(f"Policy processor error: {e}")
                await outgoing_queue.put(None)
                raise

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
            # Record responses after streaming completes
            await record.record_response(ingress_chunks, egress_chunks)

    async def _dispatch_policy_hooks(
        self,
        ctx: StreamingResponseContext,
        prev_just_completed: StreamBlock | None,
        keepalive: Callable[[], None],
    ):
        """
        Dispatch policy hooks based on state transitions.

        Checks current state and calls appropriate policy methods.
        """
        state = ctx.ingress_state

        # Always call on_chunk_received
        await self.policy.on_chunk_received(ctx)
        keepalive()

        # Call delta hooks based on current block type
        if state.current_block:
            if isinstance(state.current_block, ContentStreamBlock):
                await self.policy.on_content_delta(ctx)
                keepalive()
            elif isinstance(state.current_block, ToolCallStreamBlock):
                await self.policy.on_tool_call_delta(ctx)
                keepalive()

        # Call completion hooks based on just_completed
        if state.just_completed and state.just_completed != prev_just_completed:
            if isinstance(state.just_completed, ContentStreamBlock):
                await self.policy.on_content_complete(ctx)
                keepalive()
            elif isinstance(state.just_completed, ToolCallStreamBlock):
                await self.policy.on_tool_call_complete(ctx)
                keepalive()

        # Call finish_reason hook
        if state.finish_reason:
            await self.policy.on_finish_reason(ctx)
            keepalive()

    async def process_full_response(
        self,
        request: RequestMessage,
        transaction_id: str,
        span: Span,
    ) -> ModelResponse:
        """
        Process non-streaming response through policy.

        Flow:
        1. Call LLM (non-streaming)
        2. Apply policy.process_full_response
        3. Record original + final response

        Args:
            request: Final request to send to LLM
            transaction_id: Transaction ID
            span: OpenTelemetry span

        Returns:
            Final response (after policy modification)
        """
        # Create transaction record
        record = TransactionRecord(
            transaction_id=transaction_id,
            db_pool=self.db_pool,
            event_publisher=self.event_publisher,
        )

        # Call LLM
        original_response = await self.llm_client.complete(request)

        # Create policy context
        context = PolicyContext(
            call_id=transaction_id,
            span=span,
            request=request,
        )

        # Apply policy
        final_response = await self.policy.process_full_response(
            original_response,
            context,
        )

        # Record (non-streaming uses direct responses, not chunks)
        await record.record_response([original_response], [final_response])

        return final_response
```

**Key Design:**
- **Reuses `StreamingOrchestrator`** - no reimplementation of timeout/queue logic
- **Direct state access** - policy sees current state, not snapshots
- **Simple dispatch** - checks state transitions, calls policy methods
- **Buffering for recording** - collects chunks for TransactionRecord
- **Keepalive calls** - prevents timeout during policy processing
- Uses existing `TaskGroup`, timeout monitoring, error handling from orchestrator

**Location:** `src/luthien_proxy/v2/orchestrator.py`

---

### 7. Policy Interface Updates

**Updated EventBasedPolicy with direct state access:**

```python
class EventBasedPolicy:
    """Base class for event-driven policies with direct state access."""

    async def on_request(
        self,
        request: RequestMessage,
        context: PolicyContext,
    ) -> RequestMessage:
        """Process request before sending to LLM."""
        return request

    async def on_chunk_received(
        self,
        ctx: StreamingResponseContext,
    ):
        """
        Called on every chunk received from LLM.

        Policy can access:
        - ctx.ingress_state.raw_chunks[-1] - latest chunk
        - ctx.ingress_state.current_block - block being assembled
        - ctx.ingress_state.blocks - all completed blocks
        """
        pass

    async def on_content_delta(
        self,
        ctx: StreamingResponseContext,
    ):
        """
        Called when content delta received.

        Default: passthrough latest chunk to egress.
        """
        chunk = get_last_ingress_chunk(ctx)
        if chunk:
            await send_chunk(ctx, chunk)

    async def on_content_complete(
        self,
        ctx: StreamingResponseContext,
    ):
        """
        Called when content block completes.

        Policy can access:
        - ctx.ingress_state.just_completed - the completed block
        """
        pass

    async def on_tool_call_delta(
        self,
        ctx: StreamingResponseContext,
    ):
        """
        Called when tool call delta received.

        Default: passthrough latest chunk to egress.
        """
        chunk = get_last_ingress_chunk(ctx)
        if chunk:
            await send_chunk(ctx, chunk)

    async def on_tool_call_complete(
        self,
        ctx: StreamingResponseContext,
    ):
        """
        Called when tool call block completes.

        Policy can access:
        - ctx.ingress_state.just_completed - the completed tool call block
        """
        pass

    async def on_finish_reason(
        self,
        ctx: StreamingResponseContext,
    ):
        """
        Called when finish_reason received.

        Policy can access:
        - ctx.ingress_state.finish_reason
        """
        pass

    async def on_stream_complete(
        self,
        ctx: StreamingResponseContext,
    ):
        """
        Called when stream completes.

        Policy can do final processing or validation.
        """
        pass

    async def process_full_response(
        self,
        response: ModelResponse,
        context: PolicyContext,
    ) -> ModelResponse:
        """Process complete (non-streaming) response."""
        return response
```

**Key Changes:**
- Context now has `ingress` and `egress` assemblers directly
- Policy reads current state (never stale)
- Policy pushes to egress via helpers or direct `ctx.egress.process(chunk)`
- Default implementations use helpers for passthrough

**Example Policy:**

```python
class UppercasePolicy(EventBasedPolicy):
    """Example: uppercase all content."""

    async def on_content_delta(self, ctx):
        # Get current content
        if ctx.ingress_state.current_block:
            text = ctx.ingress_state.current_block.content

            # Modify
            upper = text.upper()

            # Send to egress
            await send_text(ctx, upper)
```

---

## Data Flow

### Non-Streaming Request Flow

```
1. Client sends request (OpenAI or Anthropic format)
   ↓
2. Gateway: openai_chat_completions() or anthropic_messages()
   - Generate call_id, create span
   - Verify auth
   ↓
3. Gateway: Convert to OpenAI format if needed
   - anthropic_to_openai_request() for /v1/messages
   - No conversion for /v1/chat/completions
   ↓
4. PolicyOrchestrator.process_request(request, call_id, span)
   - Create TransactionRecord
   - Create PolicyContext
   - Call policy.on_request(request, context)
   - Record original + final request
   - Return final_request
   ↓
5. PolicyOrchestrator.process_full_response(final_request, call_id, span)
   - Call llm_client.complete(final_request)
   - Create PolicyContext
   - Call policy.process_full_response(response, context)
   - Record original + final response
   - Return final_response
   ↓
6. Gateway: Convert to client format if needed
   - openai_to_anthropic_response() for /v1/messages
   - No conversion for /v1/chat/completions
   ↓
7. Gateway: Return JSONResponse
```

### Streaming Request Flow

```
1. Client sends request (OpenAI or Anthropic format)
   ↓
2. Gateway: openai_chat_completions() or anthropic_messages()
   - Generate call_id, create span
   - Verify auth
   ↓
3. Gateway: Convert to OpenAI format if needed
   ↓
4. PolicyOrchestrator.process_request(request, call_id, span)
   - Apply policy to request
   - Record original + final request
   ↓
5. PolicyOrchestrator.process_streaming_response(final_request, call_id, span)
   │
   ├─ Get LLM stream: llm_client.stream(final_request)
   │
   ├─ Create ingress/egress assemblers
   │
   ├─ Create StreamingResponseContext
   │
   ├─ Define policy_processor function
   │
   └─ Call StreamingOrchestrator.process(llm_stream, policy_processor, ...)
      │
      ├─ StreamingOrchestrator creates incoming/outgoing queues
      │
      ├─ Background task 1: Feed LLM chunks → incoming_queue
      │
      ├─ Background task 2: policy_processor
      │  │
      │  └─ While incoming_queue has chunks:
      │     ├─ Get chunk from incoming_queue
      │     ├─ Buffer chunk (for recording)
      │     ├─ ingress.process(chunk)
      │     ├─ Dispatch policy hooks based on state transitions
      │     │  ├─ on_chunk_received(ctx)
      │     │  ├─ on_content_delta(ctx) if content
      │     │  ├─ on_content_complete(ctx) if block done
      │     │  └─ etc.
      │     │  └─ Policy pushes to ctx.egress
      │     ├─ Get new egress chunks
      │     └─ Put egress chunks → outgoing_queue
      │
      ├─ Background task 3: Timeout monitor
      │
      └─ Main task: Drain outgoing_queue, yield chunks
         ↓
6. Gateway: Convert chunks to client format if needed
   - openai_chunk_to_anthropic_chunk() for /v1/messages
   ↓
7. Gateway: Yield as SSE (text/event-stream)
```

### Streaming Detail: Chunk Processing

```
LLM returns chunk
   ↓
StreamingOrchestrator: incoming_queue.put(chunk)
   ↓
policy_processor: chunk = await incoming_queue.get()
   ↓
ingress_chunks.append(chunk)  # Buffer for recording
   ↓
await ingress.process(chunk)
   ├─ state.raw_chunks.append(chunk)
   ├─ Parse into blocks
   └─ Update state.current_block, state.just_completed
   ↓
Dispatch policy hooks based on state:
   ├─ await policy.on_chunk_received(ctx)
   ├─ await policy.on_content_delta(ctx) if content chunk
   └─ await policy.on_content_complete(ctx) if block finished
   ↓
Policy methods execute:
   async def on_content_delta(self, ctx):
       text = ctx.ingress_state.current_block.content.upper()
       await send_text(ctx, text)
   ↓
send_text helper:
   chunk = create_text_chunk(text)
   await ctx.egress.process(chunk)
   ↓
egress.process(chunk):
   ├─ state.raw_chunks.append(chunk)
   └─ Parse into blocks
   ↓
policy_processor: Check egress for new chunks
   new_chunks = egress.state.raw_chunks[len(egress_chunks):]
   ↓
policy_processor: Push to outgoing_queue
   for chunk in new_chunks:
       egress_chunks.append(chunk)
       await outgoing_queue.put(chunk)
   ↓
StreamingOrchestrator: Drain outgoing_queue
   chunk = await outgoing_queue.get()
   yield chunk
   ↓
Gateway receives chunk
   ↓
Convert to client format if needed
   ↓
Yield as SSE to client
```

---

## Format Conversion Points

### Conversion Map

```
Client Request (Anthropic or OpenAI)
   ↓
[GATEWAY: anthropic_to_openai_request if /v1/messages]
   ↓
OpenAI format (internal)
   ↓
PolicyOrchestrator (OpenAI format)
   ↓
Policy (OpenAI format)
   ↓
LLMClient.stream/complete (OpenAI → LiteLLM)
   ↓
LiteLLM returns OpenAI format (ModelResponse)
   ↓
ingress assembler (OpenAI format)
   ↓
Policy sees OpenAI format
   ↓
egress assembler (OpenAI format)
   ↓
[GATEWAY: openai_chunk_to_anthropic_chunk if /v1/messages]
   ↓
Client Format (Anthropic or OpenAI)
```

**Key Points:**
- Conversion happens at gateway edges only
- Policy always works with OpenAI format
- litellm returns OpenAI format (no conversion needed after LLM)
- Existing conversion functions reused (no changes needed)

---

## Critical Issues Resolution

### Issue 1: Policies Can't Push Data ✅ FIXED

**Problem:** v1 spec had no way for policy to push to egress.

**Solution:** Context has direct reference to egress assembler:
```python
@dataclass
class StreamingResponseContext:
    egress: StreamingChunkAssembler  # Policy pushes here

# Policy can:
await ctx.egress.process(chunk)
# Or use helper:
await send_text(ctx, "text")
```

### Issue 2: Event State Mutation Race ✅ FIXED

**Problem:** v1 spec enqueued events with mutable state reference, causing stale reads.

**Solution:** No event queue! Policy methods called directly with context that has **current state reference**:
```python
# Always current, never stale
state = ctx.ingress.state
current_block = state.current_block
```

### Issue 3: Timeout Tracking Dead Code ✅ FIXED

**Problem:** v1 spec created TimeoutTracker but never launched monitor task.

**Solution:** Use existing `StreamingOrchestrator` which properly:
- Creates TimeoutTracker
- Launches monitor task: `tg.create_task(timeout_tracker.raise_on_timeout())`
- Cancels monitor on completion
- Calls `keepalive()` to prevent timeout

### Issue 4: Unbounded Queues ✅ FIXED

**Problem:** v1 spec used `asyncio.Queue()` with no maxsize.

**Solution:** Use existing `StreamingOrchestrator` which:
- Uses unbounded queues by design (simpler, works for our use case)
- Natural backpressure: if outgoing_queue fills, policy_processor blocks on `put()`
- If this becomes an issue, can add maxsize in one place (StreamingOrchestrator)

**Note:** Unbounded queues are acceptable because:
- Policy processing is fast (minimal transformation)
- LLM stream rate is bounded (network limited)
- If backpressure needed, add maxsize to StreamingOrchestrator queues

### Issue 5: Duplicate Orchestration ✅ FIXED

**Problem:** v1 spec reimplemented queue coordination, timeout, task management.

**Solution:** Reuse existing `StreamingOrchestrator`:
- Don't create new `StreamingResponse` class
- Use proven timeout/queue/task logic
- PolicyOrchestrator is thin coordinator using existing pieces

---

## Implementation Plan

### Phase 1: Foundation (Week 1)

#### Task 1.1: LLMClient Interface + Implementation

**Files:**
- NEW: `src/luthien_proxy/v2/llm/client.py`
- NEW: `src/luthien_proxy/v2/llm/litellm_client.py`

**Steps:**
1. Define `LLMClient` ABC
2. Implement `LiteLLMClient`
3. Write unit tests (mock litellm)

**Tests:**
- Mock litellm.acompletion
- Verify streaming and non-streaming

**Acceptance:**
- [ ] LLMClient ABC defined
- [ ] LiteLLMClient works
- [ ] Tests pass

#### Task 1.2: StreamingResponseContext + Helpers

**Files:**
- NEW: `src/luthien_proxy/v2/streaming/streaming_response_context.py`
- NEW: `src/luthien_proxy/v2/streaming/helpers.py`

**Steps:**
1. Define `StreamingResponseContext` dataclass
2. Implement helpers: `send_text()`, `send_chunk()`, etc.
3. Write unit tests

**Tests:**
- Mock assemblers
- Verify helpers work

**Acceptance:**
- [ ] Context has required fields
- [ ] Helpers send to egress correctly

#### Task 1.3: TransactionRecord

**Files:**
- NEW: `src/luthien_proxy/v2/transaction_record.py`

**Steps:**
1. Implement `TransactionRecord` class
2. Methods: `record_request()`, `record_response()`
3. Wire up existing emission functions
4. Write unit tests

**Tests:**
- Mock db_pool, event_publisher
- Verify events emitted

**Acceptance:**
- [ ] Records requests and responses
- [ ] Emits to DB and Redis
- [ ] Non-blocking

### Phase 2: PolicyOrchestrator (Week 2)

#### Task 2.1: Request Processing

**Files:**
- NEW: `src/luthien_proxy/v2/orchestrator.py`

**Steps:**
1. Create `PolicyOrchestrator` class
2. Implement `process_request()` method
3. Write unit tests

**Tests:**
- Mock policy, verify flow
- Verify recording

**Acceptance:**
- [ ] Applies policy to request
- [ ] Records original + final
- [ ] Tests pass

#### Task 2.2: Non-Streaming Response

**Files:**
- MODIFY: `src/luthien_proxy/v2/orchestrator.py`

**Steps:**
1. Implement `process_full_response()` method
2. Wire up LLMClient, policy, TransactionRecord
3. Write unit tests

**Tests:**
- Mock LLMClient
- Verify full flow

**Acceptance:**
- [ ] Calls LLMClient.complete()
- [ ] Applies policy
- [ ] Records responses
- [ ] Tests pass

#### Task 2.3: Streaming Response

**Files:**
- MODIFY: `src/luthien_proxy/v2/orchestrator.py`

**Steps:**
1. Implement `process_streaming_response()` method
2. Define `policy_processor` function
3. Implement `_dispatch_policy_hooks()` method
4. Use existing `StreamingOrchestrator`
5. Write unit tests

**Tests:**
- Mock LLMClient streaming
- Test policy dispatch
- Test passthrough
- Test modification
- Test error handling

**Acceptance:**
- [ ] Uses StreamingOrchestrator correctly
- [ ] Dispatches policy hooks based on state
- [ ] Policy can push to egress
- [ ] Chunks flow through correctly
- [ ] Timeout monitoring works
- [ ] Tests pass

### Phase 3: Gateway Integration (Week 3)

#### Task 3.1: Update OpenAI Endpoint

**Files:**
- MODIFY: `src/luthien_proxy/v2/gateway_routes.py`

**Steps:**
1. Create `PolicyOrchestrator` instance
2. Replace ControlPlane with orchestrator
3. Update streaming and non-streaming paths
4. Remove old code

**Tests:**
- Integration tests
- Full end-to-end

**Acceptance:**
- [ ] OpenAI endpoint works
- [ ] Both streaming and non-streaming
- [ ] Tests pass

#### Task 3.2: Update Anthropic Endpoint

**Files:**
- MODIFY: `src/luthien_proxy/v2/gateway_routes.py`

**Steps:**
1. Use orchestrator (same as OpenAI)
2. Keep format conversion at edges
3. Test with Anthropic client

**Tests:**
- Integration tests
- Format conversion tests

**Acceptance:**
- [ ] Anthropic endpoint works
- [ ] Format conversion correct
- [ ] Tests pass

#### Task 3.3: Remove Old Code

**Files:**
- MODIFY: `src/luthien_proxy/v2/control/synchronous_control_plane.py`

**Steps:**
1. Remove duplicate methods from ControlPlane
2. Keep PolicyContext creation if reused
3. Update imports

**Acceptance:**
- [ ] No duplicate logic
- [ ] Tests pass

### Phase 4: Policy Updates (Week 4)

#### Task 4.1: Update EventBasedPolicy

**Files:**
- MODIFY: `src/luthien_proxy/v2/policies/event_based_policy.py`

**Steps:**
1. Update method signatures to use `StreamingResponseContext`
2. Update default implementations to use helpers
3. Update docstrings

**Acceptance:**
- [ ] All methods use new context
- [ ] Defaults use helpers
- [ ] Compiles

#### Task 4.2: Update Existing Policies

**Files:**
- MODIFY: All policies in `src/luthien_proxy/v2/policies/`

**Steps:**
1. Update each policy to use new context
2. Replace old streaming_ctx usage
3. Test each policy

**Acceptance:**
- [ ] All policies updated
- [ ] All tests pass

### Phase 5: Documentation (Week 5)

**Files:**
- UPDATE: `dev/ARCHITECTURE.md`
- UPDATE: `dev/event_driven_policy_guide.md`
- CREATE: `dev/orchestrator_guide.md`

**Steps:**
1. Document new architecture
2. Update policy guide
3. Add examples

---

## Testing Strategy

### Unit Tests

**Per Component:**
- `LiteLLMClient`: Mock litellm
- `TransactionRecord`: Mock DB/Redis
- `StreamingResponseContext`: Data structure
- `Helpers`: Mock assemblers
- `PolicyOrchestrator`: Mock all dependencies

**Coverage Target:** 90%+ for new code

### Integration Tests

**Scenarios:**
1. Full flow with NoOpPolicy
2. Full flow with modifying policy
3. Streaming with early termination
4. Error handling
5. Format conversion

### End-to-End Tests

**Scenarios:**
1. Real HTTP → policy → LLM → response
2. Anthropic client → OpenAI backend
3. Concurrent requests
4. Long streams (timeout)

---

## Migration Path

### Step 1: Parallel Implementation

- Implement new components
- No changes to gateway yet
- Test components in isolation

### Step 2: Gateway Switch

- Update gateway to use PolicyOrchestrator
- Keep ControlPlane for backward compat
- Monitor staging

### Step 3: Policy Migration

- Update policy interface
- Migrate policies one by one
- Test thoroughly

### Step 4: Deprecation

- Mark ControlPlane deprecated
- Remove after stability period
- Update docs

### Rollback Plan

- Gateway can switch back to ControlPlane
- Each component independently rollback-able
- Monitoring for issues

---

## Success Criteria

### Functional

- [ ] All tests pass
- [ ] OpenAI endpoint works (streaming + non-streaming)
- [ ] Anthropic endpoint works (streaming + non-streaming)
- [ ] Policies can modify content
- [ ] Policies can terminate early
- [ ] Observability events emitted
- [ ] Real-time UI works

### Non-Functional

- [ ] No performance regression
- [ ] Memory usage stable
- [ ] Code coverage >90%
- [ ] SOLID principles followed
- [ ] Clear responsibilities
- [ ] Easy to test

### Developer Experience

- [ ] Easier to write policies
- [ ] Easier to test
- [ ] Clear documentation
- [ ] Examples available

---

## Appendix: Design Decisions

### 1. Reuse StreamingOrchestrator

**Rationale:**
- Already proven (timeout, queues, task management work)
- Well tested
- Handles edge cases
- Don't reimplement what works

### 2. Direct State Access

**Rationale:**
- No deep copy overhead
- Always accurate (never stale)
- Simpler than snapshot + copy
- Policy can inspect full history

### 3. No Event Queue for Policy Dispatch

**Rationale:**
- Events with state snapshots = stale data
- Direct dispatch simpler and accurate
- No queue overhead
- Policy sees current state

### 4. Context Has Assembler References

**Rationale:**
- Policy can push to egress
- Policy can read current state
- No helper parameter gymnastics
- Clear ownership

### 5. Helpers are Functions, Not Methods

**Rationale:**
- Simpler to test
- Can be utility module
- Flexible (can add to context later if preferred)
- Functional style is clear

---

## Related Documents

- [dev/state-refactoring-plan.md](./state-refactoring-plan.md) - Original plan
- [dev/gateway-end-to-end-flow.md](./gateway-end-to-end-flow.md) - Current flow
- [dev/pipeline-architecture-solid-analysis.md](./pipeline-architecture-solid-analysis.md) - SOLID analysis
- [dev/event_driven_policy_guide.md](./event_driven_policy_guide.md) - Policy guide

---

**End of Specification v2**
