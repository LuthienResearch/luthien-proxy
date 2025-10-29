# Pipeline Refactor Specification

**Date:** 2025-10-28
**Status:** Ready for Implementation
**Goal:** Refactor request/response pipeline with clear separation of concerns and event-driven coordination

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture Diagram](#architecture-diagram)
3. [Component Specifications](#component-specifications)
4. [Data Flow](#data-flow)
5. [Format Conversion Points](#format-conversion-points)
6. [Event System](#event-system)
7. [Implementation Plan](#implementation-plan)
8. [Testing Strategy](#testing-strategy)
9. [Migration Path](#migration-path)

---

## Overview

### Current Problems

1. **Mixed responsibilities:** ControlPlane handles both policy execution and observability (buffering, event emission)
2. **Scattered format conversion:** Conversion logic spread across gateway and streaming code
3. **Tight coupling:** LLM calls embedded directly in gateway, hard to test/swap
4. **Implicit state:** Request/response tracking via dict lookup instead of explicit passing

### Proposed Solution

Introduce clear architectural layers:

- **Gateway Layer:** HTTP handling + format conversion at edges
- **PolicyOrchestrator:** Coordinates flow between components
- **LLMClient:** Abstracts LLM backend calls
- **StreamingResponse:** Event-driven chunk processing
- **TransactionRecord:** Records original vs modified requests/responses
- **Policy:** Business logic for request/response modification

---

## Architecture Diagram

### Component Relationships

```
┌─────────────────────────────────────────────────────────────┐
│ Gateway Layer (HTTP)                                        │
│ - Parse HTTP request                                        │
│ - Convert client format → OpenAI format                     │
│ - Convert OpenAI format → client format                     │
│ - Return HTTP response                                      │
└────────────────────┬────────────────────────────────────────┘
                     ↓
         OpenAI-format request
                     ↓
┌─────────────────────────────────────────────────────────────┐
│ PolicyOrchestrator                                          │
│ - Owns TransactionRecord (recording)                        │
│ - Calls policy.on_request → gets modified request          │
│ - Calls LLMClient to get response                           │
│ - Creates ingress + egress StreamingResponse                │
│ - Wires ingress events → policy → egress                    │
└────┬─────────────────────┬──────────────────────────────────┘
     ↓                     ↓
  LLMClient          TransactionRecord
     │                     │
     │                     └─ Records: transaction_id,
     │                                 original_request,
     │                                 final_request,
     │                                 original_response,
     │                                 final_response
     ↓
  LiteLLM → OpenAI-format chunks
     │
     ↓
┌─────────────────────────────────────────────────────────────┐
│ Ingress StreamingResponse                                   │
│ - StreamingChunkAssembler (parse chunks → blocks)          │
│ - Queue events: chunk_received, content_delta,              │
│                 content_complete, tool_call_delta,          │
│                 tool_call_complete, stream_complete         │
│ - Timeout tracking                                          │
└────────────────────┬────────────────────────────────────────┘
                     ↓ events in queue
┌─────────────────────────────────────────────────────────────┐
│ Policy Event Processing                                     │
│ - Dequeue events from ingress                               │
│ - Build StreamingResponseContext:                           │
│     * transaction_id                                        │
│     * final_request                                         │
│     * ingress_state (StreamState)                           │
│     * egress_state (StreamState)                            │
│     * scratchpad (dict)                                     │
│     * span (OTel)                                           │
│ - Call appropriate policy method                            │
│ - Policy pushes chunks to egress via helpers                │
└────────────────────┬────────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────────────┐
│ Egress StreamingResponse                                    │
│ - Receives chunks from policy                               │
│ - Assembles into StreamState                                │
│ - Yields to gateway layer                                   │
└────────────────────┬────────────────────────────────────────┘
                     ↓
         OpenAI-format chunks
                     ↓
                  Gateway
                     ↓
         Client-format chunks
```

### Streaming Flow Detail

```
LLM chunks → ingress.add_chunk(chunk)
                ↓
          ingress.assembler.process(chunk)
                ↓
          state.raw_chunks.append(chunk)
          state.blocks updated
                ↓
          events enqueued:
            - chunk_received
            - content_delta (if content chunk)
            - content_complete (if block finished)
            - tool_call_delta (if tool chunk)
            - tool_call_complete (if tool finished)
                ↓
PolicyOrchestrator._process_events:
    event_type, state = await ingress.queue.get()

    if event_type == "chunk_received":
        await policy.on_chunk_received(ctx)
    elif event_type == "content_delta":
        await policy.on_content_delta(ctx)
    elif event_type == "content_complete":
        await policy.on_content_complete(ctx)
    # ... etc

Policy methods:
    async def on_content_delta(self, ctx):
        # Decide what to send to client
        await send_text(ctx, modified_text, egress)
                ↓
          egress.add_chunk(chunk)
                ↓
          egress.assembler.process(chunk)
                ↓
          yield to gateway
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
            redis_conn=None,  # Use event_publisher for Redis
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
        original: ModelResponse,
        final: ModelResponse,
    ):
        """Record original and final response, emit events."""
        self.original_response = original
        self.final_response = final

        # Emit to DB (queued, non-blocking)
        emit_response_event(
            call_id=self.transaction_id,
            original_response=original.model_dump(),
            final_response=final.model_dump(),
            db_pool=self.db_pool,
            redis_conn=None,
        )

        # Publish real-time event
        if self.event_publisher:
            await self.event_publisher.publish_event(
                call_id=self.transaction_id,
                event_type="transaction.response_recorded",
                data={
                    "original_finish_reason": self._get_finish_reason(original),
                    "final_finish_reason": self._get_finish_reason(final),
                },
            )

    def _get_finish_reason(self, response: ModelResponse) -> str | None:
        """Extract finish_reason from response."""
        choices = response.model_dump().get("choices", [])
        return choices[0].get("finish_reason") if choices else None
```

**Key Design Decisions:**
- Combines data storage + emission logic (acceptable since logic is simple)
- Owned by PolicyOrchestrator (orchestrator knows when to record)
- Non-blocking event emission (queued for background processing)

---

### 2. StreamingChunkAssembler

**Purpose:** Parse raw chunks into structured blocks + maintain raw_chunks

**Type:** Pure state management + parsing

```python
class StreamingChunkAssembler:
    """
    Parses streaming chunks into structured blocks.

    Maintains:
    - blocks: List of ContentStreamBlock, ToolCallStreamBlock
    - current_block: Block currently being assembled
    - just_completed: Block that just finished (cleared after processing)
    - raw_chunks: All raw chunks received
    - finish_reason: From final chunk
    """

    def __init__(self):
        self.state = StreamState()

    async def process(self, chunk: ModelResponse):
        """
        Process a chunk:
        1. Append to raw_chunks
        2. Parse and update blocks
        3. Set just_completed if block finished
        """
        # Store raw chunk
        self.state.raw_chunks.append(chunk)

        # Parse into blocks
        self._update_state(chunk)

    def _update_state(self, chunk: ModelResponse):
        """Update state.blocks based on chunk content."""
        # Existing logic from current StreamingChunkAssembler
        # Detects:
        # - Content deltas → update ContentStreamBlock
        # - Tool call deltas → update ToolCallStreamBlock
        # - Block completion → set just_completed
```

**No changes needed** - this is our current `StreamingChunkAssembler` (renamed from `StreamProcessor`)

---

### 3. StreamingResponse

**Purpose:** Event-driven wrapper around StreamingChunkAssembler

**Type:** Event coordination via queues

```python
class StreamingResponse:
    """
    Wraps StreamingChunkAssembler and fires events via queue.

    Events emitted:
    - chunk_received: On every chunk
    - content_delta: On content chunk
    - content_complete: When content block finishes
    - tool_call_delta: On tool call chunk
    - tool_call_complete: When tool call block finishes
    - stream_complete: When stream ends (or timeout)
    """

    def __init__(self, timeout_seconds: float = 30.0):
        self.assembler = StreamingChunkAssembler()
        self.queue: asyncio.Queue[tuple[str, StreamState]] = asyncio.Queue()
        self.timeout_tracker = TimeoutTracker(timeout_seconds)
        self._complete = False

    async def add_chunk(self, chunk: ModelResponse):
        """
        Add a chunk:
        1. Process through assembler
        2. Enqueue events based on state changes
        3. Update timeout tracker
        """
        if self._complete:
            raise StreamingError("Cannot add chunk after stream marked complete")

        # Ping timeout tracker
        self.timeout_tracker.ping()

        # Get state before processing
        prev_just_completed = self.assembler.state.just_completed

        # Process chunk
        await self.assembler.process(chunk)
        state = self.assembler.state

        # Always enqueue chunk_received
        await self.queue.put(("chunk_received", state))

        # Enqueue type-specific delta events
        if state.current_block:
            if isinstance(state.current_block, ContentStreamBlock):
                delta = self._extract_content_delta(chunk)
                if delta:
                    await self.queue.put(("content_delta", state))
            elif isinstance(state.current_block, ToolCallStreamBlock):
                await self.queue.put(("tool_call_delta", state))

        # Enqueue completion events
        if state.just_completed and state.just_completed != prev_just_completed:
            if isinstance(state.just_completed, ContentStreamBlock):
                await self.queue.put(("content_complete", state))
            elif isinstance(state.just_completed, ToolCallStreamBlock):
                await self.queue.put(("tool_call_complete", state))

        # Enqueue finish_reason
        if state.finish_reason:
            await self.queue.put(("finish_reason", state))

    async def mark_complete(self):
        """Mark stream as complete, enqueue final event."""
        if not self._complete:
            self._complete = True
            await self.queue.put(("stream_complete", self.assembler.state))

    def is_complete(self) -> bool:
        """Check if stream is complete."""
        return self._complete

    def _extract_content_delta(self, chunk: ModelResponse) -> str | None:
        """Extract content text delta from chunk."""
        # Existing logic from EventBasedPolicy._extract_content_delta
```

**Key Design Decisions:**
- Uses TimeoutTracker (existing class) for timeout monitoring
- Queue-based event emission (not direct callbacks)
- Enqueues both granular events (delta) and completion events
- Policy can mark egress complete explicitly

---

### 4. StreamingResponseContext

**Purpose:** Context passed to policy methods during streaming

**Type:** Immutable dataclass

```python
@dataclass
class StreamingResponseContext:
    """
    Context for policy invocations during streaming.

    Contains references to ingress/egress state, request data,
    scratchpad for policy state, and OTel span.
    """

    transaction_id: str
    final_request: RequestMessage  # Request sent to LLM
    ingress_state: StreamState  # State from LLM response
    egress_state: StreamState  # State being sent to client
    scratchpad: dict[str, Any]  # Policy-specific state
    span: Span  # OpenTelemetry span for tracing
```

**Helper Functions (not methods on context):**

```python
async def send_text(
    ctx: StreamingResponseContext,
    text: str,
    egress: StreamingResponse,
):
    """Helper to send text chunk to egress."""
    from luthien_proxy.v2.policies.utils import create_text_chunk
    chunk = create_text_chunk(text)
    await egress.add_chunk(chunk)

async def send_chunk(
    ctx: StreamingResponseContext,
    chunk: ModelResponse,
    egress: StreamingResponse,
):
    """Helper to send chunk to egress."""
    await egress.add_chunk(chunk)

async def mark_egress_complete(
    ctx: StreamingResponseContext,
    egress: StreamingResponse,
):
    """Helper to mark egress stream complete."""
    await egress.mark_complete()
```

**Key Design Decisions:**
- Context is data-only (no methods)
- Helper functions take context + egress as parameters
- Can be separate utility module or methods on context (implementation choice)

---

### 5. LLMClient (Abstract)

**Purpose:** Abstract interface for LLM backend calls

**Type:** Abstract Base Class

```python
from abc import ABC, abstractmethod

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
from litellm.types.utils import ModelResponse

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
            # litellm already returns OpenAI-format ModelResponse
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

**Key Design Decisions:**
- Abstract interface enables testing with mock clients
- Can swap LLM backends (Anthropic direct, OpenAI direct, etc.)
- litellm already returns OpenAI format - no conversion needed
- Client is responsible for setting stream=True/False

---

### 6. PolicyOrchestrator

**Purpose:** Coordinate flow between components

**Type:** Orchestrator / Coordinator

```python
from opentelemetry import trace

tracer = trace.get_tracer(__name__)

class PolicyOrchestrator:
    """
    Orchestrates request/response flow through policy layer.

    Responsibilities:
    - Own TransactionRecord for observability
    - Apply policy to requests
    - Coordinate streaming via ingress/egress
    - Wire ingress events → policy methods → egress output
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
        request: RequestMessage,  # Final request (after policy.process_request)
        transaction_id: str,
        span: Span,
    ) -> AsyncIterator[ModelResponse]:
        """
        Process streaming response through policy.

        Flow:
        1. Create ingress + egress StreamingResponse
        2. Stream from LLM → ingress
        3. Process ingress events → call policy → egress
        4. Yield from egress

        Args:
            request: Final request to send to LLM
            transaction_id: Transaction ID
            span: OpenTelemetry span

        Yields:
            ModelResponse chunks (OpenAI format)
        """
        # Create ingress and egress
        ingress = StreamingResponse(timeout_seconds=30.0)
        egress = StreamingResponse(timeout_seconds=30.0)

        # Create context
        context = StreamingResponseContext(
            transaction_id=transaction_id,
            final_request=request,
            ingress_state=ingress.assembler.state,
            egress_state=egress.assembler.state,
            scratchpad={},
            span=span,
        )

        # Start background tasks
        feed_task = asyncio.create_task(
            self._feed_ingress(request, ingress),
            name=f"feed_ingress_{transaction_id}",
        )
        process_task = asyncio.create_task(
            self._process_events(ingress, egress, context),
            name=f"process_events_{transaction_id}",
        )

        # Yield from egress
        try:
            while True:
                # Wait for egress to have chunks or complete
                if egress.is_complete() and egress.queue.empty():
                    break

                try:
                    # Get next event from egress
                    event_type, state = await asyncio.wait_for(
                        egress.queue.get(),
                        timeout=1.0,
                    )

                    # If chunk_received event, yield the latest chunk
                    if event_type == "chunk_received" and state.raw_chunks:
                        yield state.raw_chunks[-1]

                except asyncio.TimeoutError:
                    # No chunks yet, keep waiting
                    continue
        finally:
            # Ensure tasks are cleaned up
            feed_task.cancel()
            process_task.cancel()

            # Wait for cancellation
            await asyncio.gather(feed_task, process_task, return_exceptions=True)

    async def _feed_ingress(
        self,
        request: RequestMessage,
        ingress: StreamingResponse,
    ):
        """
        Feed LLM chunks into ingress StreamingResponse.

        Background task that:
        1. Calls LLM via llm_client.stream()
        2. Feeds each chunk to ingress.add_chunk()
        3. Marks ingress complete when done
        """
        try:
            async for chunk in self.llm_client.stream(request):
                await ingress.add_chunk(chunk)

            # Mark ingress complete
            await ingress.mark_complete()

        except Exception as e:
            logger.error(f"Error feeding ingress: {e}")
            await ingress.mark_complete()
            raise

    async def _process_events(
        self,
        ingress: StreamingResponse,
        egress: StreamingResponse,
        context: StreamingResponseContext,
    ):
        """
        Process events from ingress, call policy, results go to egress.

        Background task that:
        1. Dequeues events from ingress
        2. Calls appropriate policy method
        3. Policy pushes to egress via helpers
        4. Marks egress complete when ingress completes
        """
        try:
            while True:
                event_type, state = await ingress.queue.get()

                if event_type == "chunk_received":
                    await self.policy.on_chunk_received(context)

                elif event_type == "content_delta":
                    await self.policy.on_content_delta(context)

                elif event_type == "content_complete":
                    await self.policy.on_content_complete(context)

                elif event_type == "tool_call_delta":
                    await self.policy.on_tool_call_delta(context)

                elif event_type == "tool_call_complete":
                    await self.policy.on_tool_call_complete(context)

                elif event_type == "finish_reason":
                    await self.policy.on_finish_reason(context)

                elif event_type == "stream_complete":
                    await self.policy.on_stream_complete(context)

                    # Mark egress complete (unless policy already did)
                    if not egress.is_complete():
                        await egress.mark_complete()

                    break

        except Exception as e:
            logger.error(f"Error processing events: {e}")
            # Ensure egress is marked complete on error
            if not egress.is_complete():
                await egress.mark_complete()
            raise

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

        # Record
        await record.record_response(original_response, final_response)

        return final_response
```

**Key Design Decisions:**
- Owns TransactionRecord creation
- Uses LLMClient abstraction (mockable for tests)
- Background tasks for feed_ingress and process_events
- Yields from egress queue (decouples ingress from egress)
- Handles cleanup on errors and completion

---

### 7. Policy Interface Updates

**Current interface remains mostly the same:**

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

    async def on_chunk_received(
        self,
        ctx: StreamingResponseContext,
    ):
        """Called on every chunk received from LLM."""
        pass

    async def on_content_delta(
        self,
        ctx: StreamingResponseContext,
    ):
        """Called when content delta received."""
        # Default: passthrough
        if ctx.ingress_state.current_block:
            chunk = ctx.ingress_state.raw_chunks[-1]
            await send_chunk(ctx, chunk, egress)

    async def on_content_complete(
        self,
        ctx: StreamingResponseContext,
    ):
        """Called when content block completes."""
        pass

    async def on_tool_call_delta(
        self,
        ctx: StreamingResponseContext,
    ):
        """Called when tool call delta received."""
        # Default: passthrough
        if ctx.ingress_state.current_block:
            chunk = ctx.ingress_state.raw_chunks[-1]
            await send_chunk(ctx, chunk, egress)

    async def on_tool_call_complete(
        self,
        ctx: StreamingResponseContext,
    ):
        """Called when tool call block completes."""
        pass

    async def on_finish_reason(
        self,
        ctx: StreamingResponseContext,
    ):
        """Called when finish_reason received."""
        pass

    async def on_stream_complete(
        self,
        ctx: StreamingResponseContext,
    ):
        """Called when stream completes."""
        pass

    async def process_full_response(
        self,
        response: ModelResponse,
        context: PolicyContext,
    ) -> ModelResponse:
        """Process complete (non-streaming) response."""
        return response
```

**Key Changes from Current:**
- Context is now `StreamingResponseContext` (has ingress_state, egress_state)
- Policy doesn't receive `streaming_ctx` parameter (uses helpers + context)
- Policy pushes to egress via helper functions (not methods on streaming_ctx)
- Default implementations use helper functions to passthrough

---

## Data Flow

### Non-Streaming Request Flow

```
1. Client sends request (OpenAI or Anthropic format)
   ↓
2. Gateway: openai_chat_completions() or anthropic_messages()
   ↓
3. Gateway: Convert to OpenAI format if needed
   ↓
4. PolicyOrchestrator.process_request(request, transaction_id, span)
   ├─ Create TransactionRecord
   ├─ Create PolicyContext
   ├─ Call policy.on_request(request, context)
   └─ Record original + final request
   ↓
5. PolicyOrchestrator.process_full_response(final_request, transaction_id, span)
   ├─ Call llm_client.complete(final_request)
   ├─ Create PolicyContext
   ├─ Call policy.process_full_response(response, context)
   └─ Record original + final response
   ↓
6. Gateway: Convert to client format if needed
   ↓
7. Gateway: Return JSONResponse
```

### Streaming Request Flow

```
1. Client sends request (OpenAI or Anthropic format)
   ↓
2. Gateway: openai_chat_completions() or anthropic_messages()
   ↓
3. Gateway: Convert to OpenAI format if needed
   ↓
4. PolicyOrchestrator.process_request(request, transaction_id, span)
   ├─ Create TransactionRecord
   ├─ Create PolicyContext
   ├─ Call policy.on_request(request, context)
   └─ Record original + final request
   ↓
5. PolicyOrchestrator.process_streaming_response(final_request, transaction_id, span)
   ├─ Create ingress StreamingResponse
   ├─ Create egress StreamingResponse
   ├─ Create StreamingResponseContext
   ├─ Launch background task: _feed_ingress
   │  ├─ llm_client.stream(final_request)
   │  ├─ For each chunk:
   │  │  └─ ingress.add_chunk(chunk)
   │  │     ├─ assembler.process(chunk)
   │  │     └─ Enqueue events
   │  └─ ingress.mark_complete()
   ├─ Launch background task: _process_events
   │  ├─ Dequeue event from ingress.queue
   │  ├─ Call policy method (on_chunk_received, on_content_delta, etc.)
   │  ├─ Policy pushes to egress via helpers
   │  └─ When ingress complete, mark egress complete
   └─ Yield from egress.queue
      ↓
6. Gateway: Convert chunks to client format if needed
   ↓
7. Gateway: Yield as SSE (text/event-stream)
```

### Streaming Detail: Ingress → Policy → Egress

```
LLM returns chunk
   ↓
ingress.add_chunk(chunk)
   ├─ assembler.process(chunk)
   │  ├─ state.raw_chunks.append(chunk)
   │  └─ Update state.blocks
   └─ Enqueue events:
      ├─ ("chunk_received", state)
      ├─ ("content_delta", state) if content chunk
      └─ ("content_complete", state) if block finished
   ↓
PolicyOrchestrator._process_events:
   event_type, state = await ingress.queue.get()

   if event_type == "content_delta":
       await policy.on_content_delta(ctx)
   ↓
policy.on_content_delta(ctx):
   # Policy decides what to send
   modified_text = ctx.ingress_state.current_block.content.upper()
   await send_text(ctx, modified_text, egress)
   ↓
send_text helper:
   chunk = create_text_chunk(text)
   await egress.add_chunk(chunk)
   ↓
egress.add_chunk(chunk)
   ├─ assembler.process(chunk)
   └─ Enqueue ("chunk_received", state)
   ↓
PolicyOrchestrator.process_streaming_response:
   event_type, state = await egress.queue.get()
   if event_type == "chunk_received":
       yield state.raw_chunks[-1]
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
OpenAI format
   ↓
PolicyOrchestrator (works with OpenAI)
   ↓
Policy (works with OpenAI)
   ↓
LLMClient.stream/complete (OpenAI → LiteLLM)
   ↓
LiteLLM returns OpenAI format (ModelResponse)
   ↓
ingress StreamingResponse (OpenAI format)
   ↓
Policy sees OpenAI format
   ↓
egress StreamingResponse (OpenAI format)
   ↓
[GATEWAY: openai_chunk_to_anthropic_chunk if /v1/messages]
   ↓
Client Format (Anthropic or OpenAI)
```

### Gateway Layer Conversions

**Request Conversion:**

```python
# In gateway openai_chat_completions()
data = await request.json()
# data is already OpenAI format, no conversion needed
request_message = RequestMessage(**data)

# In gateway anthropic_messages()
anthropic_data = await request.json()
openai_data = anthropic_to_openai_request(anthropic_data)
request_message = RequestMessage(**openai_data)
```

**Response Conversion (Streaming):**

```python
# In gateway openai_chat_completions()
egress_stream = await orchestrator.process_streaming_response(...)
async for chunk in egress_stream:
    # chunk is ModelResponse (OpenAI format)
    # No conversion needed for OpenAI endpoint
    yield f"data: {chunk.model_dump_json()}\n\n"

# In gateway anthropic_messages()
egress_stream = await orchestrator.process_streaming_response(...)
async for chunk in egress_stream:
    # chunk is ModelResponse (OpenAI format)
    # Convert to Anthropic format
    anthropic_chunk = openai_chunk_to_anthropic_chunk(chunk)
    # Format as Anthropic SSE
    event_type = anthropic_chunk.get("type", "content_block_delta")
    yield f"event: {event_type}\ndata: {json.dumps(anthropic_chunk)}\n\n"
```

**Response Conversion (Non-Streaming):**

```python
# In gateway openai_chat_completions()
final_response = await orchestrator.process_full_response(...)
# final_response is ModelResponse (OpenAI format)
return JSONResponse(final_response.model_dump())

# In gateway anthropic_messages()
final_response = await orchestrator.process_full_response(...)
# Convert to Anthropic format
anthropic_response = openai_to_anthropic_response(final_response)
return JSONResponse(anthropic_response)
```

### Format Conversion Functions

Located in `v2/llm/format_converters.py` (already exist):
- `anthropic_to_openai_request(data: dict) -> dict`
- `openai_to_anthropic_response(response: ModelResponse) -> dict`
- `openai_chunk_to_anthropic_chunk(chunk: ModelResponse) -> dict`

No changes needed to these functions.

---

## Event System

### Event Types

| Event Type | When Fired | Contains |
|------------|-----------|----------|
| `chunk_received` | Every chunk added to StreamingResponse | Current StreamState |
| `content_delta` | Content chunk received | StreamState with current ContentStreamBlock |
| `content_complete` | Content block finished | StreamState with just_completed ContentStreamBlock |
| `tool_call_delta` | Tool call chunk received | StreamState with current ToolCallStreamBlock |
| `tool_call_complete` | Tool call block finished | StreamState with just_completed ToolCallStreamBlock |
| `finish_reason` | finish_reason received | StreamState with finish_reason |
| `stream_complete` | Stream ended | Final StreamState |

### Event Flow Diagram

```
StreamingResponse.add_chunk(chunk)
   ↓
assembler.process(chunk)
   ├─ state.raw_chunks.append(chunk)
   ├─ Parse into blocks
   └─ Update state.current_block, state.just_completed
   ↓
Enqueue events based on state changes:
   ├─ Always: ("chunk_received", state)
   ├─ If current_block is ContentStreamBlock: ("content_delta", state)
   ├─ If current_block is ToolCallStreamBlock: ("tool_call_delta", state)
   ├─ If just_completed is ContentStreamBlock: ("content_complete", state)
   ├─ If just_completed is ToolCallStreamBlock: ("tool_call_complete", state)
   └─ If finish_reason: ("finish_reason", state)
   ↓
StreamingResponse.mark_complete()
   └─ Enqueue: ("stream_complete", state)
```

### Event Processing

```python
async def _process_events(
    self,
    ingress: StreamingResponse,
    egress: StreamingResponse,
    context: StreamingResponseContext,
):
    while True:
        event_type, state = await ingress.queue.get()

        # Call appropriate policy method
        if event_type == "chunk_received":
            await self.policy.on_chunk_received(context)
        elif event_type == "content_delta":
            await self.policy.on_content_delta(context)
        elif event_type == "content_complete":
            await self.policy.on_content_complete(context)
        elif event_type == "tool_call_delta":
            await self.policy.on_tool_call_delta(context)
        elif event_type == "tool_call_complete":
            await self.policy.on_tool_call_complete(context)
        elif event_type == "finish_reason":
            await self.policy.on_finish_reason(context)
        elif event_type == "stream_complete":
            await self.policy.on_stream_complete(context)

            # Mark egress complete if policy hasn't
            if not egress.is_complete():
                await egress.mark_complete()

            break
```

### Queue-Based Coordination

**Why queues?**

1. **Decoupling:** Ingress and egress operate independently
2. **Backpressure:** If policy processes slower than LLM sends, queue buffers
3. **Timeout tracking:** Can monitor queue activity for timeouts
4. **Fan-out:** Policy can send 0, 1, or N chunks per input
5. **Async coordination:** Background tasks communicate via queues

**Queue behavior:**

- `ingress.queue`: Events from LLM chunks
- `egress.queue`: Events from policy output
- Both use `asyncio.Queue` (unbounded by default)
- PolicyOrchestrator dequeues from ingress, policy pushes to egress
- Gateway dequeues from egress to yield to client

---

## Implementation Plan

### Phase 1: Core Components (Week 1)

**Goal:** Implement new components without changing existing code

#### Task 1.1: LLMClient Interface + Implementation

**Files:**
- NEW: `src/luthien_proxy/v2/llm/client.py`
- NEW: `src/luthien_proxy/v2/llm/litellm_client.py`

**Steps:**
1. Define `LLMClient` ABC with `stream()` and `complete()` methods
2. Implement `LiteLLMClient` using existing litellm calls
3. Write unit tests with mock LLM responses

**Tests:**
- `tests/unit_tests/v2/llm/test_litellm_client.py`
- Mock litellm.acompletion, verify correct calls
- Test both streaming and non-streaming

**Acceptance:**
- [ ] LLMClient ABC defined
- [ ] LiteLLMClient passes tests
- [ ] Can swap with mock client for testing

#### Task 1.2: StreamingResponse

**Files:**
- NEW: `src/luthien_proxy/v2/streaming/streaming_response.py`
- MODIFY: `src/luthien_proxy/v2/streaming/streaming_chunk_assembler.py` (minor updates if needed)

**Steps:**
1. Create `StreamingResponse` class wrapping `StreamingChunkAssembler`
2. Implement event queuing (chunk_received, content_delta, etc.)
3. Add timeout tracking using existing `TimeoutTracker`
4. Write unit tests

**Tests:**
- `tests/unit_tests/v2/streaming/test_streaming_response.py`
- Feed chunks, verify events enqueued correctly
- Test timeout behavior
- Test mark_complete

**Acceptance:**
- [ ] StreamingResponse enqueues all event types
- [ ] Timeout tracking works
- [ ] Can consume events from queue

#### Task 1.3: StreamingResponseContext + Helpers

**Files:**
- NEW: `src/luthien_proxy/v2/streaming/streaming_response_context.py`
- NEW: `src/luthien_proxy/v2/streaming/helpers.py`

**Steps:**
1. Define `StreamingResponseContext` dataclass
2. Implement helper functions: `send_text()`, `send_chunk()`, `mark_egress_complete()`
3. Write unit tests

**Tests:**
- `tests/unit_tests/v2/streaming/test_helpers.py`
- Mock egress, verify helpers work correctly

**Acceptance:**
- [ ] Context has all required fields
- [ ] Helpers send chunks to egress correctly

#### Task 1.4: TransactionRecord

**Files:**
- NEW: `src/luthien_proxy/v2/transaction_record.py`

**Steps:**
1. Implement `TransactionRecord` class
2. Methods: `record_request()`, `record_response()`
3. Wire up existing `emit_request_event` and `emit_response_event`
4. Write unit tests

**Tests:**
- `tests/unit_tests/v2/test_transaction_record.py`
- Mock db_pool and event_publisher
- Verify events emitted correctly

**Acceptance:**
- [ ] Records original + final request/response
- [ ] Emits to DB and Redis
- [ ] Non-blocking (queued emission)

### Phase 2: PolicyOrchestrator (Week 2)

**Goal:** Create orchestrator, connect components

#### Task 2.1: PolicyOrchestrator - Request Processing

**Files:**
- NEW: `src/luthien_proxy/v2/orchestrator.py`

**Steps:**
1. Create `PolicyOrchestrator` class
2. Implement `process_request()` method
3. Wire up: TransactionRecord, PolicyContext, policy.on_request
4. Write unit tests

**Tests:**
- `tests/unit_tests/v2/test_orchestrator_request.py`
- Mock policy, verify request flow
- Verify recording happens

**Acceptance:**
- [ ] Applies policy to request
- [ ] Records original + final request
- [ ] Returns final request

#### Task 2.2: PolicyOrchestrator - Non-Streaming Response

**Files:**
- MODIFY: `src/luthien_proxy/v2/orchestrator.py`

**Steps:**
1. Implement `process_full_response()` method
2. Wire up: LLMClient, policy.process_full_response, TransactionRecord
3. Write unit tests

**Tests:**
- `tests/unit_tests/v2/test_orchestrator_non_streaming.py`
- Mock LLMClient, verify complete flow
- Verify recording happens

**Acceptance:**
- [ ] Calls LLMClient.complete()
- [ ] Applies policy to response
- [ ] Records original + final response
- [ ] Returns final response

#### Task 2.3: PolicyOrchestrator - Streaming Response

**Files:**
- MODIFY: `src/luthien_proxy/v2/orchestrator.py`

**Steps:**
1. Implement `process_streaming_response()` method
2. Create ingress + egress StreamingResponse
3. Implement `_feed_ingress()` background task
4. Implement `_process_events()` background task
5. Write unit tests

**Tests:**
- `tests/unit_tests/v2/test_orchestrator_streaming.py`
- Mock LLMClient streaming, verify event flow
- Test policy passthrough
- Test policy modification
- Test error handling

**Acceptance:**
- [ ] Feeds LLM chunks to ingress
- [ ] Processes events and calls policy
- [ ] Yields from egress
- [ ] Handles errors and cleanup

### Phase 3: Gateway Integration (Week 3)

**Goal:** Integrate PolicyOrchestrator into gateway, remove old code

#### Task 3.1: Update Gateway Routes - OpenAI Endpoint

**Files:**
- MODIFY: `src/luthien_proxy/v2/gateway_routes.py`

**Steps:**
1. Create `PolicyOrchestrator` instance (use app state or create per-request)
2. Replace current flow with orchestrator calls
3. Keep format conversion at edges (none needed for OpenAI)
4. Remove old ControlPlane usage

**Changes:**
```python
@router.post("/v1/chat/completions")
async def openai_chat_completions(request: Request, ...):
    # Parse request (OpenAI format)
    data = await request.json()
    request_message = RequestMessage(**data)

    # Create orchestrator
    orchestrator = PolicyOrchestrator(
        policy=control_plane.policy,  # Reuse policy from old control plane
        llm_client=LiteLLMClient(),
        db_pool=db_pool,
        event_publisher=event_publisher,
    )

    # Process request
    final_request = await orchestrator.process_request(
        request_message,
        call_id,
        span,
    )

    if final_request.stream:
        # Streaming
        egress_stream = await orchestrator.process_streaming_response(
            final_request,
            call_id,
            span,
        )

        async def format_stream():
            async for chunk in egress_stream:
                yield f"data: {chunk.model_dump_json()}\n\n"

        return FastAPIStreamingResponse(format_stream(), media_type="text/event-stream")
    else:
        # Non-streaming
        final_response = await orchestrator.process_full_response(
            final_request,
            call_id,
            span,
        )
        return JSONResponse(final_response.model_dump())
```

**Tests:**
- `tests/integration_tests/v2/test_gateway_openai.py`
- Full end-to-end with real policy
- Verify format, recording, policy application

**Acceptance:**
- [ ] OpenAI endpoint works with orchestrator
- [ ] Both streaming and non-streaming
- [ ] All tests pass

#### Task 3.2: Update Gateway Routes - Anthropic Endpoint

**Files:**
- MODIFY: `src/luthien_proxy/v2/gateway_routes.py`

**Steps:**
1. Use orchestrator (same as OpenAI)
2. Keep format conversion: anthropic → OpenAI (input), OpenAI → anthropic (output)
3. Remove old ControlPlane usage

**Changes:**
```python
@router.post("/v1/messages")
async def anthropic_messages(request: Request, ...):
    # Parse and convert to OpenAI format
    anthropic_data = await request.json()
    openai_data = anthropic_to_openai_request(anthropic_data)
    request_message = RequestMessage(**openai_data)

    # Create orchestrator
    orchestrator = PolicyOrchestrator(...)

    # Process request
    final_request = await orchestrator.process_request(...)

    if final_request.stream:
        # Streaming
        egress_stream = await orchestrator.process_streaming_response(...)

        async def format_stream():
            # Add message_start event
            yield anthropic_message_start_event(call_id, model)

            async for chunk in egress_stream:
                # Convert OpenAI → Anthropic
                anthropic_chunk = openai_chunk_to_anthropic_chunk(chunk)
                event_type = anthropic_chunk.get("type", "content_block_delta")
                yield f"event: {event_type}\ndata: {json.dumps(anthropic_chunk)}\n\n"

            # Add message_stop event
            yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"

        return FastAPIStreamingResponse(format_stream(), media_type="text/event-stream")
    else:
        # Non-streaming
        final_response = await orchestrator.process_full_response(...)
        # Convert OpenAI → Anthropic
        anthropic_response = openai_to_anthropic_response(final_response)
        return JSONResponse(anthropic_response)
```

**Tests:**
- `tests/integration_tests/v2/test_gateway_anthropic.py`
- Full end-to-end with real policy
- Verify Anthropic format conversion

**Acceptance:**
- [ ] Anthropic endpoint works with orchestrator
- [ ] Format conversion correct
- [ ] All tests pass

#### Task 3.3: Remove Old Code

**Files:**
- DELETE methods from: `src/luthien_proxy/v2/control/synchronous_control_plane.py`
- KEEP: `SynchronousControlPlane` as thin wrapper (if other code depends on it)

**Steps:**
1. Remove `process_streaming_response` from ControlPlane (replaced by orchestrator)
2. Remove `process_full_response` from ControlPlane
3. Remove `process_request` if using orchestrator version
4. Remove buffering/event emission code
5. Keep PolicyContext creation if reused

**Acceptance:**
- [ ] No duplicate logic between ControlPlane and Orchestrator
- [ ] All tests pass
- [ ] Gateway only uses Orchestrator

### Phase 4: Policy Updates (Week 4)

**Goal:** Update policy interface to use StreamingResponseContext

#### Task 4.1: Update EventBasedPolicy Base Class

**Files:**
- MODIFY: `src/luthien_proxy/v2/policies/event_based_policy.py`

**Steps:**
1. Change method signatures to use `StreamingResponseContext`
2. Remove `streaming_ctx` parameter (use context + helpers instead)
3. Update default implementations to use helpers
4. Update docstrings

**Acceptance:**
- [ ] All methods use new context
- [ ] Default implementations work with helpers
- [ ] Backwards compatibility maintained where possible

#### Task 4.2: Update Existing Policies

**Files:**
- MODIFY: All policy files in `src/luthien_proxy/v2/policies/`

**Steps:**
1. Update each policy to use `StreamingResponseContext`
2. Replace `streaming_ctx.send()` with `send_chunk(ctx, chunk, egress)`
3. Replace `streaming_ctx.send_text()` with `send_text(ctx, text, egress)`
4. Update scratchpad access

**Acceptance:**
- [ ] All policies compile
- [ ] All policy tests pass
- [ ] No references to old StreamingContext

### Phase 5: Documentation & Cleanup (Week 5)

**Goal:** Document new architecture, remove dead code

#### Task 5.1: Update Documentation

**Files:**
- UPDATE: `dev/ARCHITECTURE.md`
- UPDATE: `dev/event_driven_policy_guide.md`
- CREATE: `dev/orchestrator_guide.md`

**Steps:**
1. Document PolicyOrchestrator usage
2. Update policy writing guide with new context
3. Add examples using helper functions

#### Task 5.2: Delete Obsolete Code

**Files:**
- Review and remove unused code from ControlPlane
- Remove duplicate event emission logic
- Clean up imports

#### Task 5.3: Performance Testing

**Steps:**
1. Run load tests comparing old vs new
2. Verify no performance regression
3. Check memory usage (queue buffering)

---

## Testing Strategy

### Unit Tests

**Per Component:**
- `LiteLLMClient`: Mock litellm, verify calls
- `StreamingResponse`: Feed chunks, verify events
- `TransactionRecord`: Mock DB/Redis, verify emission
- `StreamingResponseContext`: Data structure tests
- `Helpers`: Mock egress, verify chunk sending
- `PolicyOrchestrator`: Mock all dependencies, test coordination

**Coverage Target:** 90%+ for new code

### Integration Tests

**Scenarios:**
1. Full request flow with NoOpPolicy
2. Full request flow with content-modifying policy
3. Streaming flow with early termination
4. Error handling (LLM fails, policy raises exception)
5. Format conversion (Anthropic ↔ OpenAI)

**Files:**
- `tests/integration_tests/v2/test_orchestrator_integration.py`
- `tests/integration_tests/v2/test_gateway_integration.py`

### End-to-End Tests

**Scenarios:**
1. Real HTTP request → policy → LLM → response
2. Anthropic client → OpenAI backend
3. Multiple concurrent requests
4. Long-running streams (timeout testing)

**Files:**
- `tests/e2e_tests/test_full_pipeline.py`

### Backwards Compatibility Tests

**Ensure:**
- Existing policies still work
- Event format unchanged (for observability UI)
- Database schema unchanged
- Redis events unchanged

---

## Migration Path

### Step 1: Parallel Implementation

- Implement new components alongside existing ControlPlane
- No changes to gateway or policies yet
- Run tests to validate components in isolation

### Step 2: Gateway Switch

- Update gateway to use PolicyOrchestrator
- Keep ControlPlane for backwards compatibility (deprecated)
- Monitor for issues in staging

### Step 3: Policy Migration

- Update policy interface
- Migrate policies one by one
- Test each policy thoroughly

### Step 4: Deprecation

- Mark ControlPlane as deprecated
- Remove old code after 2 sprints of stability
- Update all documentation

### Rollback Plan

**If issues found:**
1. Revert gateway changes (use ControlPlane again)
2. Fix issues in orchestrator
3. Re-deploy when stable

**Each component can be rolled back independently:**
- Gateway can switch back to ControlPlane
- LLMClient can be replaced without touching orchestrator
- TransactionRecord can be disabled without breaking flow

---

## Success Criteria

### Functional Requirements

- [ ] All existing tests pass
- [ ] OpenAI endpoint works (streaming + non-streaming)
- [ ] Anthropic endpoint works (streaming + non-streaming)
- [ ] Policies can modify content
- [ ] Policies can terminate early
- [ ] Observability events still emitted correctly
- [ ] Real-time UI still works

### Non-Functional Requirements

- [ ] No performance regression (<5% latency increase)
- [ ] Memory usage stable (queue buffering monitored)
- [ ] Code coverage >90% for new components
- [ ] SOLID principles followed (Grade B+ or better)
- [ ] Clear component responsibilities
- [ ] Easy to test with mocks

### Developer Experience

- [ ] Easier to write policies (clear context)
- [ ] Easier to test (mock LLMClient, orchestrator)
- [ ] Clear architecture documentation
- [ ] Examples for common patterns

---

## Appendix: Key Design Decisions

### 1. PolicyOrchestrator Owns TransactionRecord

**Rationale:**
- Orchestrator knows when to record (after policy decisions)
- Simple recording logic doesn't justify separate component
- Can extract to TransactionRecorder later if needed

### 2. Queue-Based Event System

**Rationale:**
- Handles backpressure (policy slower than LLM)
- Enables timeout tracking
- Decouples ingress from egress
- Supports fan-out (0, 1, N outputs per input)

### 3. Helper Functions vs Context Methods

**Rationale:**
- Keeps context lightweight (just data)
- Helpers can be utility functions or separate module
- Easier to test helpers in isolation
- Flexible implementation (can make them methods later)

### 4. LiteLLM Returns OpenAI Format

**Fact verified:** litellm always returns `ModelResponse` (OpenAI format)

**Implication:** No conversion needed after LLM calls

### 5. Format Conversion at Gateway Edges

**Rationale:**
- Policies work with one format (OpenAI)
- Conversion is endpoint-specific
- Gateway already handles HTTP, format is related
- Can extract to middleware later if needed

### 6. StreamingResponse Uses StreamingChunkAssembler

**Rationale:**
- Reuse existing parsing logic
- StreamingResponse adds event coordination
- Clear separation: parsing vs coordination

---

## Related Documents

- [dev/state-refactoring-plan.md](./state-refactoring-plan.md) - Original refactoring plan
- [dev/gateway-end-to-end-flow.md](./gateway-end-to-end-flow.md) - Current flow diagram
- [dev/pipeline-architecture-solid-analysis.md](./pipeline-architecture-solid-analysis.md) - SOLID analysis
- [dev/event_driven_policy_guide.md](./event_driven_policy_guide.md) - Policy writing guide
- [dev/ARCHITECTURE.md](./ARCHITECTURE.md) - Overall architecture

---

**End of Specification**
