# Streaming Policy Architecture Plan

## Motivation

### The Problem

The current policy architecture has a fundamental limitation: policies process each streaming chunk independently via HTTP POST requests. This creates several issues:

1. **No stateful context**: Policies cannot maintain state across chunks (e.g., counting tokens, buffering content)
2. **Thread safety issues**: Using instance variables like `self.token_count` is not thread-safe when multiple streams share the same policy instance
3. **Limited control**: Policies can only transform chunks 1:1; they cannot:
   - Buffer multiple chunks before responding
   - Replace the entire stream with content from a different source
   - Emit chunks at a different pace than they're received
   - Generate responses independently of the incoming stream

### What We Really Need

Policies should be able to **take over response generation entirely**. For example:

- **Content filtering policy**: Buffer tokens until detecting a policy violation, then replace the entire response with "Request blocked"
- **Model routing policy**: Receive initial chunks, decide they're low quality, abort and call a different LLM entirely
- **Compression policy**: Buffer 10 chunks, summarize them, emit 1 summary chunk
- **A/B testing policy**: Ignore incoming stream, call two different models, compare outputs, return the better one

The key insight: **The incoming stream from the LLM and the outgoing stream to the client should be independent.**

## Architecture Goals

1. **Bidirectional streaming**: Callback can send chunks to control plane AND receive independent chunks back
2. **Stateful per-stream contexts**: Each stream maintains isolated state that persists across chunks
3. **Thread safety**: Multiple concurrent streams don't interfere with each other
4. **Low latency**: Target <10ms overhead per chunk for simple policies
5. **Simple for common cases**: Transform-style policies should be easy to write
6. **Powerful for complex cases**: Policies can do arbitrary async operations
7. **Clear migration path**: Start simple (single instance), scale later (multi-instance)

## Proposed Solution: WebSocket-Based Streaming (Phase 1)

### High-Level Flow

```
LiteLLM Callback                    Control Plane
     |                                    |
     |-- WebSocket Connect ------------->|
     |   (with stream_id)                 |
     |                                    |
     |-- START message ------------------>| Create policy context
     |   (request metadata)               |
     |                                    |
     |-- CHUNK message ------------------>| Policy observes chunk
     |   (LLM token)                      |
     |                                    |
     |<-- CHUNK message -------------------| Policy emits response
     |    (transformed token)             |
     |                                    |
     |-- CHUNK message ------------------>| Policy observes chunk
     |                                    |
     |<-- CHUNK message -------------------| Policy emits response
     |                                    |
     |-- END message --------------------->| Cleanup context
     |                                    |
     |-- WebSocket Close ----------------->|
```

**Key characteristics:**
- **Single persistent connection** per stream
- **Messages flow independently** in both directions
- **Policy context** lives in memory on control plane for duration of connection
- **Connection close** triggers automatic cleanup

### Policy API

#### Base Policy Class

```python
from abc import ABC
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional, Any
import time

@dataclass
class StreamPolicyContext:
    """Base class for per-stream policy state.

    Must be serializable (for future Redis migration).
    Subclass to add policy-specific state.

    Attributes:
        stream_id: Unique identifier for this stream
        original_request: Initial request metadata
        chunk_count: Number of chunks processed (policies should increment manually)
        start_time: When the stream started (set automatically at creation)
    """
    stream_id: str
    original_request: dict[str, Any]
    chunk_count: int = 0
    start_time: float = field(default_factory=time.time)

class LuthienPolicy(ABC):
    """Base policy class with streaming support."""

    def create_stream_context(self, stream_id: str, request_data: dict) -> StreamPolicyContext:
        """Create per-stream state. Called once when stream starts.

        Override to return custom context subclass with policy-specific state.
        Default returns empty base context.
        """
        return StreamPolicyContext(stream_id=stream_id, original_request=request_data)

    async def generate_response_stream(
        self,
        context: StreamPolicyContext,
        incoming_stream: AsyncIterator[dict],
    ) -> AsyncIterator[dict]:
        """Generate response stream independently of incoming stream.

        Args:
            context: Per-stream state (created by create_stream_context)
            incoming_stream: Async iterator of chunks from LLM

        Yields:
            Response chunks to send back to client

        The policy can:
        - Process incoming_stream and yield transformed chunks
        - Ignore incoming_stream and yield entirely different content
        - Buffer/batch/filter chunks as needed
        - Call other services asynchronously
        """
        raise NotImplementedError
```

#### Example: StreamingSeparatorPolicy

```python
@dataclass
class SeparatorStreamContext(StreamPolicyContext):
    """Context for separator policy."""
    every_n: int
    separator_str: str
    token_count: int = 0  # Per-stream state!

class StreamingSeparatorPolicy(LuthienPolicy):
    def __init__(self, options: Optional[dict[str, Any]] = None):
        opts = options or {}
        self.every_n = opts.get("every_n", 1)
        self.separator_str = opts.get("separator_str", " | ")

    def create_stream_context(self, stream_id: str, request_data: dict) -> SeparatorStreamContext:
        return SeparatorStreamContext(
            stream_id=stream_id,
            original_request=request_data,
            every_n=self.every_n,
            separator_str=self.separator_str,
        )

    async def generate_response_stream(
        self,
        context: SeparatorStreamContext,
        incoming_stream: AsyncIterator[dict],
    ) -> AsyncIterator[dict]:
        """Add separator every N tokens."""
        async for chunk in incoming_stream:
            # Increment base class chunk_count for tracking
            context.chunk_count += 1

            try:
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta", {})
                    content = delta.get("content")

                    if content:
                        context.token_count += 1
                        if context.token_count % context.every_n == 0:
                            delta["content"] = content + context.separator_str

                yield chunk
            except Exception:
                # On error, pass through original
                yield chunk
```

#### Example: Model Replacement Policy

```python
@dataclass
class ReplacementContext(StreamPolicyContext):
    """Context for model replacement policy."""
    incoming_complete: bool = False
    buffered_chunks: list = field(default_factory=list)

class ModelReplacementPolicy(LuthienPolicy):
    """Replace incoming stream with output from a different model."""

    def __init__(self, options: Optional[dict[str, Any]] = None):
        opts = options or {}
        self.replacement_model = opts.get("replacement_model", "gpt-4")

    def create_stream_context(self, stream_id: str, request_data: dict) -> ReplacementContext:
        return ReplacementContext(stream_id=stream_id, original_request=request_data)

    async def generate_response_stream(
        self,
        context: ReplacementContext,
        incoming_stream: AsyncIterator[dict],
    ) -> AsyncIterator[dict]:
        """Ignore incoming stream, call different model instead."""

        # Consume incoming stream in background (so it doesn't block)
        async def observe_incoming():
            try:
                async for chunk in incoming_stream:
                    context.buffered_chunks.append(chunk)
            except Exception as exc:
                logger.error(f"Error observing incoming stream: {exc}")
            finally:
                context.incoming_complete = True

        # Keep task handle to avoid unobserved exceptions
        observer_task = asyncio.create_task(observe_incoming())

        try:
            # Call replacement model and yield its chunks
            replacement_request = {
                **context.original_request,
                "model": self.replacement_model,
            }

            async for chunk in self._call_replacement_model(replacement_request):
                yield chunk
        finally:
            # Ensure observer task completes before we exit
            # Shield from cancellation during cleanup
            try:
                await asyncio.shield(observer_task)
            except asyncio.CancelledError:
                # If we're cancelled during shield, cancel the observer too
                observer_task.cancel()
                try:
                    await observer_task
                except asyncio.CancelledError:
                    pass

    async def _call_replacement_model(self, request: dict) -> AsyncIterator[dict]:
        """Call the replacement model."""
        # Implementation: call LiteLLM or other service
        ...
```

#### Policy Development Best Practices

When implementing `generate_response_stream()`:

1. **Always increment `chunk_count`**: Policies should increment `context.chunk_count` for each chunk processed from `incoming_stream`. This enables accurate logging and metrics.

   ```python
   async for chunk in incoming_stream:
       context.chunk_count += 1  # Track chunks for metrics/logging
       # ... process chunk ...
       yield transformed_chunk
   ```

2. **Handle background tasks properly**: If spawning async tasks, always:
   - Store the task handle: `task = asyncio.create_task(...)`
   - Shield from cancellation: `await asyncio.shield(task)` in finally
   - Cancel if needed: Handle `CancelledError` and call `task.cancel()`

3. **Error handling**: Wrap chunk processing in try/except to avoid breaking the stream on errors. Fall back to pass-through when possible.

4. **State isolation**: Never use `self` variables for per-stream state. Always use `context` attributes to ensure thread safety across concurrent streams.

### Control Plane Implementation

#### WebSocket Endpoint

```python
# control_plane/streaming_routes.py
from fastapi import WebSocket, WebSocketDisconnect
from typing import Dict
import asyncio
import json

# In-memory store of active stream contexts
_active_streams: Dict[str, StreamPolicyContext] = {}

@app.websocket("/stream/{stream_id}")
async def policy_stream_endpoint(websocket: WebSocket, stream_id: str):
    """WebSocket endpoint for policy streaming."""
    await websocket.accept()

    try:
        # Receive START message with request metadata
        start_msg = await websocket.receive_json()
        if start_msg.get("type") != "START":
            await websocket.close(code=1002, reason="Expected START message")
            return

        # Create policy context
        policy = get_active_policy()
        context = policy.create_stream_context(stream_id, start_msg.get("data", {}))
        _active_streams[stream_id] = context

        # Create incoming stream from WebSocket
        incoming_stream = _incoming_stream_from_websocket(websocket, stream_id)

        # Run policy's response stream
        try:
            async for response_chunk in policy.generate_response_stream(context, incoming_stream):
                await websocket.send_json({
                    "type": "CHUNK",
                    "data": response_chunk,
                })
        except Exception as exc:
            logger.error(f"Error in policy stream {stream_id}: {exc}")
            await websocket.send_json({
                "type": "ERROR",
                "error": str(exc),
            })

    except WebSocketDisconnect:
        logger.info(f"Client disconnected from stream {stream_id}")

    finally:
        # Cleanup
        _active_streams.pop(stream_id, None)
        await websocket.close()

async def _incoming_stream_from_websocket(
    websocket: WebSocket,
    stream_id: str
) -> AsyncIterator[dict]:
    """Convert WebSocket messages into an async iterator."""
    try:
        while True:
            msg = await websocket.receive_json()

            if msg.get("type") == "CHUNK":
                yield msg.get("data")

            elif msg.get("type") == "END":
                # Incoming stream complete
                break

            elif msg.get("type") == "ERROR":
                logger.error(f"Error from client on stream {stream_id}: {msg.get('error')}")
                break

    except WebSocketDisconnect:
        # Client disconnected mid-stream
        logger.info(f"Client disconnected during stream {stream_id}")
```

### LiteLLM Callback Implementation

**Critical Requirement**: The WebSocket must persist across all chunk invocations for a stream. We need a connection manager.

#### Connection Manager

```python
# proxy/stream_connection_manager.py
from typing import Dict, Optional
import websockets
import asyncio
import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

@dataclass
class StreamConnection:
    """Manages a persistent WebSocket connection for a stream."""
    stream_id: str
    websocket: websockets.WebSocketClientProtocol
    outgoing_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    incoming_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    sender_task: Optional[asyncio.Task] = None
    receiver_task: Optional[asyncio.Task] = None
    error: Optional[Exception] = None

    async def send_chunk(self, chunk: dict) -> None:
        """Queue a chunk to send to control plane."""
        await self.outgoing_queue.put(chunk)

    async def receive_chunk(self, timeout: float = 5.0) -> Optional[dict]:
        """Receive a transformed chunk from control plane."""
        try:
            return await asyncio.wait_for(
                self.incoming_queue.get(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            return None

    async def _sender_loop(self):
        """Background task that sends queued chunks to control plane."""
        try:
            while True:
                chunk = await self.outgoing_queue.get()
                if chunk is None:  # Sentinel for shutdown
                    break
                await self.websocket.send(json.dumps(chunk))
        except Exception as exc:
            self.error = exc
            logger.error(f"Sender error for stream {self.stream_id}: {exc}")

    async def _receiver_loop(self):
        """Background task that receives chunks from control plane."""
        try:
            async for message in self.websocket:
                data = json.loads(message)
                await self.incoming_queue.put(data)
        except Exception as exc:
            self.error = exc
            logger.error(f"Receiver error for stream {self.stream_id}: {exc}")

    def start_background_tasks(self):
        """Start sender and receiver background tasks."""
        self.sender_task = asyncio.create_task(self._sender_loop())
        self.receiver_task = asyncio.create_task(self._receiver_loop())

    async def close(self):
        """Clean shutdown of connection and background tasks."""
        # Signal sender to stop
        await self.outgoing_queue.put(None)

        # Wait for sender to finish
        if self.sender_task:
            try:
                await asyncio.wait_for(self.sender_task, timeout=5.0)
            except asyncio.TimeoutError:
                self.sender_task.cancel()

        # Close WebSocket (will stop receiver)
        try:
            await self.websocket.close()
        except Exception:
            pass

        # Wait for receiver to finish
        if self.receiver_task:
            try:
                await asyncio.wait_for(self.receiver_task, timeout=5.0)
            except asyncio.TimeoutError:
                self.receiver_task.cancel()


class StreamConnectionManager:
    """Manages persistent WebSocket connections keyed by stream_id."""

    def __init__(self, control_plane_url: str):
        self.control_plane_url = control_plane_url
        self._connections: Dict[str, StreamConnection] = {}
        self._lock = asyncio.Lock()

    async def get_or_create_connection(
        self,
        stream_id: str,
        request_data: dict
    ) -> StreamConnection:
        """Get existing connection or create new one for stream_id."""
        async with self._lock:
            if stream_id in self._connections:
                conn = self._connections[stream_id]
                # Check if connection is still alive
                if conn.error:
                    # Connection failed, clean up and recreate
                    await self._cleanup_connection(stream_id)
                else:
                    return conn

            # Create new connection
            ws_url = self.control_plane_url.replace("http://", "ws://").replace("https://", "wss://")
            ws_url = f"{ws_url}/stream/{stream_id}"

            try:
                websocket = await websockets.connect(ws_url)
            except Exception as exc:
                logger.error(f"Failed to connect to control plane for {stream_id}: {exc}")
                raise

            # Send START message
            await websocket.send(json.dumps({
                "type": "START",
                "data": request_data,
            }))

            # Create connection object
            conn = StreamConnection(stream_id=stream_id, websocket=websocket)
            conn.start_background_tasks()

            self._connections[stream_id] = conn
            return conn

    async def close_connection(self, stream_id: str):
        """Close and remove a connection."""
        async with self._lock:
            await self._cleanup_connection(stream_id)

    async def _cleanup_connection(self, stream_id: str):
        """Internal: cleanup connection (must hold lock)."""
        if stream_id in self._connections:
            conn = self._connections.pop(stream_id)
            try:
                await conn.close()
            except Exception as exc:
                logger.error(f"Error closing connection {stream_id}: {exc}")
```

#### Callback Integration

```python
# proxy/litellm_callback.py (updated)
from typing import Optional, Any, Set
import logging
import os

logger = logging.getLogger(__name__)

# Global connection manager
_connection_manager: Optional[StreamConnectionManager] = None

def get_connection_manager() -> StreamConnectionManager:
    """Get global connection manager singleton."""
    global _connection_manager
    if _connection_manager is None:
        control_plane_url = os.getenv("CONTROL_PLANE_URL", "http://localhost:8001")
        _connection_manager = StreamConnectionManager(control_plane_url)
    return _connection_manager


class LuthienCallback(CustomLogger):
    def __init__(self):
        super().__init__()
        self._first_chunk_per_stream: Set[str] = set()

    async def async_post_call_streaming_iterator_hook(
        self,
        user_api_key_dict: Optional[dict],
        response: Any,
        request_data: dict,
    ) -> Optional[dict]:
        """Stream chunks through control plane via persistent WebSocket.

        This hook is called once per chunk. We maintain a persistent WebSocket
        connection across all chunks in the stream via the connection manager.
        """

        stream_id = request_data.get("litellm_call_id")
        if not stream_id:
            # No stream_id, pass through unchanged
            return response

        try:
            conn_manager = get_connection_manager()

            # Get or create persistent connection for this stream
            is_first_chunk = stream_id not in self._first_chunk_per_stream

            if is_first_chunk:
                # First chunk: create connection (sends START message internally)
                conn = await conn_manager.get_or_create_connection(stream_id, request_data)
                self._first_chunk_per_stream.add(stream_id)
            else:
                # Subsequent chunks: reuse existing connection
                # Note: get_or_create_connection will return existing if present
                conn = await conn_manager.get_or_create_connection(stream_id, request_data)

            # Check for connection errors
            if conn.error:
                logger.error(f"Connection error for {stream_id}, falling back to passthrough")
                return response

            # Send this chunk to control plane
            await conn.send_chunk({
                "type": "CHUNK",
                "data": response,
            })

            # Receive transformed chunk from control plane
            transformed = await conn.receive_chunk(timeout=5.0)

            if transformed is None:
                # Timeout or error, return original
                logger.warning(f"Timeout receiving chunk for {stream_id}, using original")
                return response

            if transformed.get("type") == "CHUNK":
                return transformed.get("data")
            elif transformed.get("type") == "ERROR":
                logger.error(f"Control plane error: {transformed.get('error')}")
                return response

            return response

        except Exception as exc:
            logger.error(f"Error in streaming hook for {stream_id}: {exc}")
            # Fallback: return original response
            return response

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        """Hook called when stream completes - cleanup connection."""
        stream_id = kwargs.get("litellm_params", {}).get("metadata", {}).get("litellm_call_id")
        if stream_id:
            try:
                conn_manager = get_connection_manager()

                # Get connection if exists
                if stream_id in conn_manager._connections:
                    conn = conn_manager._connections[stream_id]

                    # Send END message
                    await conn.send_chunk({"type": "END"})

                    # Give a moment for END to be sent
                    await asyncio.sleep(0.1)

                # Close connection
                await conn_manager.close_connection(stream_id)

                # Remove from first chunk tracking
                self._first_chunk_per_stream.discard(stream_id)
            except Exception as exc:
                logger.error(f"Error closing stream {stream_id}: {exc}")

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        """Hook called on stream failure - cleanup connection."""
        stream_id = kwargs.get("litellm_params", {}).get("metadata", {}).get("litellm_call_id")
        if stream_id:
            try:
                conn_manager = get_connection_manager()
                await conn_manager.close_connection(stream_id)
                self._first_chunk_per_stream.discard(stream_id)
            except Exception as exc:
                logger.error(f"Error cleaning up failed stream {stream_id}: {exc}")
```

**How this works:**

1. **First chunk**: `get_or_create_connection()` creates WebSocket, sends START, spawns background sender/receiver tasks
2. **Subsequent chunks**: `get_or_create_connection()` returns existing connection (no new connection created)
3. **Per chunk**: Queues chunk for sending, receives transformed chunk from queue
4. **Stream end**: Sends END message, closes connection, cleans up background tasks
5. **Connection persistence**: Same WebSocket lives for entire stream (all chunks)
6. **Bidirectional independence**: Sender and receiver run in separate tasks with queues decoupling them
7. **Thread safety**: Each stream has its own connection object with isolated state

### Message Protocol

```json
// START - Client -> Control Plane
{
  "type": "START",
  "data": {
    "model": "gpt-4",
    "messages": [...],
    "litellm_call_id": "abc123",
    ...
  }
}

// CHUNK - Bidirectional
{
  "type": "CHUNK",
  "data": {
    "choices": [{
      "delta": {"content": "hello"},
      "index": 0
    }],
    ...
  }
}

// END - Client -> Control Plane
{
  "type": "END"
}

// ERROR - Bidirectional
{
  "type": "ERROR",
  "error": "Error message"
}
```

## Rejected Alternatives

### 1. HTTP POST per Chunk (Current Approach)

**Why rejected:**
- ❌ No stateful context across chunks
- ❌ Thread safety issues
- ❌ Can't decouple input/output streams
- ❌ Can't do buffering, replacement, etc.

**When it would work:**
- Simple 1:1 transformations only
- No need for cross-chunk state

---

### 2. Redis Pub/Sub for Streaming

**Approach:**
```
Callback publishes to: stream:request:{stream_id}
Control plane subscribes to: stream:request:{stream_id}
Control plane publishes to: stream:response:{stream_id}
Callback subscribes to: stream:response:{stream_id}
State stored in: stream:context:{stream_id}
```

**Why not Phase 1:**
- ⚠️ Higher latency (~5-10ms per chunk vs ~1ms)
- ⚠️ More complexity (state serialization, lifecycle management)
- ⚠️ Coordination needed (which instance handles which stream)
- ⚠️ Overhead for single-instance deployment

**Why it's still valuable (Phase 2):**
- ✅ Horizontal scaling: any instance can handle any stream
- ✅ State persistence: survives instance crashes
- ✅ No sticky sessions needed

**Decision**: Redis pub/sub is the right choice for multi-instance scaling, but overkill for initial single-instance deployment.

---

### 3. gRPC Bidirectional Streaming

**Approach:**
```protobuf
service PolicyService {
  rpc ProcessStream(stream StreamChunk) returns (stream StreamChunk);
}
```

**Why rejected:**
- ⚠️ Major infrastructure change (protobuf, gRPC server)
- ⚠️ Less debuggable (binary protocol)
- ⚠️ Steeper learning curve
- ⚠️ Not HTTP-based (harder to integrate with existing FastAPI app)

**When it would be better:**
- Very high throughput (thousands of streams/second)
- Need strong typing guarantees
- Already using gRPC elsewhere

**Decision**: Overkill for our use case. WebSocket gives us 90% of the benefits with 10% of the complexity.

---

### 4. WebSocket + Redis State Snapshots

**Approach:**
- Use WebSocket for low-latency streaming
- Periodically snapshot context to Redis
- On crash, reconnect and resume from last snapshot

**Why rejected for Phase 1:**
- ⚠️ Complexity without full recovery guarantees
- ⚠️ Snapshot staleness (lose state since last snapshot)
- ⚠️ Partial recovery (not all policies are resumable)
- ⚠️ Overhead even when crashes don't happen
- ⚠️ Two sources of truth (memory + Redis) can diverge

**Why it might be reconsidered:**
- If crash recovery becomes critical
- If stream durations are very long (hours)
- As a middle ground before full Redis pub/sub

**Decision**: YAGNI - don't add complexity for crashes that rarely happen. Client can retry the whole request.

---

### 5. Server-Sent Events (SSE) + HTTP POST

**Approach:**
- Callback POSTs chunks to control plane
- Control plane streams responses back via SSE

**Why rejected:**
- ⚠️ Still stateless POSTs (back to original problem)
- ⚠️ Two separate connections (POST + SSE)
- ⚠️ Lifecycle management is complex
- ⚠️ No clear benefit over WebSocket

**When it would work:**
- If we only needed one-way streaming (control plane -> callback)
- If client can't do WebSocket (browser limitation)

**Decision**: WebSocket is cleaner for bidirectional streaming.

## Migration Path: Single Instance → Multi-Instance

### Phase 1: WebSocket (Now)

**Deployment:**
- Single control plane instance
- In-memory policy contexts
- WebSocket connections

**Characteristics:**
- ✅ Lowest latency (~1ms per chunk)
- ✅ Simplest implementation
- ⚠️ Instance crash = stream lost (acceptable: client retries)
- ⚠️ Horizontal scaling requires sticky sessions

**When this works:**
- Single instance or small cluster
- Short-to-medium stream duration (seconds to minutes)
- Acceptable to lose streams on crash

---

### Phase 2: Redis Pub/Sub (When Scaling Needed)

**Deployment:**
- Multiple control plane instances
- Policy contexts in Redis
- Redis pub/sub for all communication

**Changes required:**
1. Replace WebSocket endpoint with Redis subscriber workers
2. Add context serialization (already designed for this!)
3. Add stream coordinator (lock/queue to prevent duplicate processing)
4. Update callback to publish to Redis instead of WebSocket

**Characteristics:**
- ✅ True horizontal scaling
- ✅ State survives crashes
- ✅ Load distribution across instances
- ⚠️ Higher latency (~5-10ms per chunk, still <200ms target)
- ⚠️ More complex coordination

**When to migrate:**
- Running multiple control plane instances
- Need crash recovery
- Single instance is CPU bottleneck
- Observed coordination issues with sticky sessions

---

### Key Design Decision: Keep Policy API Unchanged

```python
# This code works in BOTH Phase 1 and Phase 2:
async def generate_response_stream(
    context: StreamPolicyContext,
    incoming_stream: AsyncIterator[dict],
) -> AsyncIterator[dict]:
    async for chunk in incoming_stream:
        # Transform chunk
        yield transformed_chunk
```

**The only requirement**: `StreamPolicyContext` must be serializable (use dataclasses, simple types).

This ensures:
- ✅ Policies written in Phase 1 work in Phase 2
- ✅ Testing is consistent
- ✅ Migration is infrastructure change, not policy rewrite

## Implementation Checklist

### Phase 1 (Initial Implementation)

- [ ] Update `LuthienPolicy` base class
  - [ ] Add `create_stream_context()` method
  - [ ] Add `generate_response_stream()` method
  - [ ] Keep backward compatibility with existing `async_post_call_streaming_iterator_hook`

- [ ] Create `StreamPolicyContext` base class
  - [ ] Make it a dataclass
  - [ ] Ensure serializability

- [ ] Implement WebSocket endpoint on control plane
  - [ ] `/stream/{stream_id}` endpoint
  - [ ] Message protocol (START, CHUNK, END, ERROR)
  - [ ] Context lifecycle management
  - [ ] Error handling and cleanup

- [ ] Update litellm callback
  - [ ] WebSocket connection management
  - [ ] Per-stream connection tracking
  - [ ] Fallback to pass-through on errors

- [ ] Update `StreamingSeparatorPolicy`
  - [ ] Migrate to new API
  - [ ] Use per-stream context
  - [ ] Test thread safety

- [ ] Add tests
  - [ ] Unit tests for policy context creation
  - [ ] Integration tests for WebSocket streaming
  - [ ] Test concurrent streams
  - [ ] Test error handling and cleanup

- [ ] Documentation
  - [ ] Policy developer guide
  - [ ] Migration guide for existing policies
  - [ ] Architecture diagrams

### Phase 2 (Future: Redis Pub/Sub)

- [ ] Design Redis coordination pattern
  - [ ] Stream work queue
  - [ ] Distributed locking
  - [ ] Context serialization format

- [ ] Implement Redis pub/sub transport
  - [ ] Replace WebSocket with Redis channels
  - [ ] State store implementation
  - [ ] Lifecycle management

- [ ] Add configuration
  - [ ] Feature flag for WebSocket vs Redis
  - [ ] Performance tuning parameters

- [ ] Update deployment
  - [ ] Multi-instance configuration
  - [ ] Load balancer setup
  - [ ] Monitoring and metrics

## Performance Considerations

### Latency Targets

| Component | Target | Phase 1 (WS) | Phase 2 (Redis) |
|-----------|--------|--------------|-----------------|
| Per-chunk overhead | <10ms | ~1ms | ~5-10ms |
| Context creation | <50ms | ~1ms | ~10ms |
| Total budget | <200ms | ✅ <10ms | ✅ <50ms |

### Scalability

**Phase 1 (WebSocket):**
- Single instance: ~1000 concurrent streams
- Limited by: CPU, memory, WebSocket connection limits
- Scaling: Vertical (bigger instance) or horizontal with sticky sessions

**Phase 2 (Redis):**
- Multi-instance: ~10,000+ concurrent streams
- Limited by: Redis throughput, network bandwidth
- Scaling: Horizontal (add more instances)

### Memory Usage

**Per-stream overhead:**
- Policy context: ~1-10 KB (depends on policy)
- WebSocket connection: ~50 KB
- Buffers: varies by policy

**Example:** 100 concurrent streams = ~5-10 MB memory

## Monitoring and Observability

### Metrics to Track

- `stream_duration_seconds`: How long streams stay open
- `stream_error_rate`: Percentage of streams ending in error
- `stream_concurrent_count`: Current number of active streams
- `chunk_processing_latency_ms`: Time to process each chunk
- `websocket_reconnect_count`: How often clients reconnect

### Logging

```python
logger.info(f"Stream {stream_id} started", extra={
    "stream_id": stream_id,
    "policy": policy.__class__.__name__,
    "model": request_data.get("model"),
})

logger.info(f"Stream {stream_id} completed", extra={
    "stream_id": stream_id,
    "chunk_count": context.chunk_count,
    "duration_seconds": time.time() - context.start_time,
})
```

## Testing Strategy

### Unit Tests

```python
@pytest.mark.asyncio
async def test_separator_policy_maintains_per_stream_state():
    """Test that concurrent streams have isolated state."""
    policy = StreamingSeparatorPolicy({"every_n": 2})

    # Create two independent stream contexts
    context1 = policy.create_stream_context("stream1", {})
    context2 = policy.create_stream_context("stream2", {})

    # Process chunks on stream1
    stream1_chunks = [
        {"choices": [{"delta": {"content": "a"}}]},
        {"choices": [{"delta": {"content": "b"}}]},  # Should get separator
    ]

    result1 = []
    async for chunk in policy.generate_response_stream(
        context1,
        async_iter(stream1_chunks)
    ):
        result1.append(chunk)

    # Process chunks on stream2 (should have independent count)
    stream2_chunks = [
        {"choices": [{"delta": {"content": "x"}}]},
        {"choices": [{"delta": {"content": "y"}}]},  # Should get separator
    ]

    result2 = []
    async for chunk in policy.generate_response_stream(
        context2,
        async_iter(stream2_chunks)
    ):
        result2.append(chunk)

    # Verify independent state
    assert result1[1]["choices"][0]["delta"]["content"] == "b | "
    assert result2[1]["choices"][0]["delta"]["content"] == "y | "
```

### Integration Tests

```python
@pytest.mark.asyncio
async def test_websocket_streaming_end_to_end():
    """Test full WebSocket streaming flow."""
    async with websockets.connect(f"ws://localhost:8000/stream/test123") as ws:
        # Send START
        await ws.send(json.dumps({
            "type": "START",
            "data": {"model": "gpt-4", "messages": [...]},
        }))

        # Send chunks
        await ws.send(json.dumps({
            "type": "CHUNK",
            "data": {"choices": [{"delta": {"content": "hello"}}]},
        }))

        # Receive transformed chunk
        response = json.loads(await ws.recv())
        assert response["type"] == "CHUNK"
        assert "hello" in response["data"]["choices"][0]["delta"]["content"]

        # Send END
        await ws.send(json.dumps({"type": "END"}))
```

## Security Considerations

1. **Authentication**: WebSocket connections should verify API keys
2. **Rate limiting**: Prevent abuse of WebSocket connections
3. **Resource limits**: Max concurrent streams per client
4. **Timeouts**: Auto-close streams that are idle too long
5. **Input validation**: Sanitize all incoming messages

## Open Questions

1. **Conversation context**: Eventually want to track multi-turn conversations (hours). How does this relate to per-stream contexts (minutes)?

2. **Policy composition**: Can we chain multiple policies? (e.g., filter -> transform -> log)

3. **Metrics integration**: Should policy contexts expose metrics hooks?

4. **Testing**: How do we simulate realistic streaming scenarios in tests?

## Success Criteria

Phase 1 is successful when:
- ✅ `StreamingSeparatorPolicy` works with per-stream state
- ✅ Multiple concurrent streams don't interfere
- ✅ Latency overhead <10ms per chunk
- ✅ Clean error handling and recovery
- ✅ Easy to write new streaming policies
- ✅ Clear migration path to Phase 2 documented

## References

- [WebSocket Protocol (RFC 6455)](https://datatracker.ietf.org/doc/html/rfc6455)
- [FastAPI WebSockets](https://fastapi.tiangolo.com/advanced/websockets/)
- [Redis Pub/Sub](https://redis.io/docs/manual/pubsub/)
- Current conversation streaming: [src/luthien_proxy/control_plane/conversation/streams.py](../src/luthien_proxy/control_plane/conversation/streams.py)
