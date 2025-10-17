# V2 Architecture Implementation Notes

## What's Been Done

### Core Architecture (Completed)

1. **Control Plane Interface** (`src/luthien_proxy/v2/control/`)
   - `interface.py`: Protocol definition for `ControlPlaneService` (simplified, no PolicyResult wrapper)
   - `models.py`: Pydantic models (`RequestMetadata`, `PolicyEvent`, `StreamingContext`)
   - `local.py`: In-process implementation with event collection
   - Clean separation allows future network implementation without changing gateway code

2. **Policy Abstraction** (`src/luthien_proxy/v2/policies/`)
   - `base.py`: `PolicyHandler` with event emission support
   - Policies decide what content to forward AND emit PolicyEvents describing activity
   - `DefaultPolicyHandler`: Example with token limits, content filtering, event emission
   - `noop.py`: `NoOpPolicy` for testing and baseline
   - Interface: `apply_request_policies`, `apply_response_policy`, `apply_streaming_chunk_policy`, `emit_event()`

3. **LLM Integration** (`src/luthien_proxy/v2/llm/`)
   - `format_converters.py`: OpenAI ↔ Anthropic format conversion
   - Uses LiteLLM as library instead of proxy

4. **API Gateway** (`src/luthien_proxy/v2/main.py`)
   - FastAPI application with OpenAI and Anthropic endpoints
   - Bidirectional streaming with policy control
   - Authentication with API key
   - Health check endpoint

5. **Documentation**
   - `dev/v2_architecture_design.md`: Complete architectural design doc
   - `scripts/test_v2_proxy.py`: Test script for manual verification

### Type Safety & Data Modeling

- All type checks passing with pyright
- Pydantic models instead of dataclasses (automatic serialization, no boilerplate)
- LiteLLM's incomplete type annotations handled with `Any` type aliases
- Type ignores used strategically for streaming and response handling

### Recent Refactoring (Latest)

**Simplified Policy Interface**:
- Removed `PolicyResult` wrapper - policies return content directly
- Added `PolicyEvent` model for structured event emission
- Policies now have two clear responsibilities:
  1. Decide what content to forward (transform/filter/validate)
  2. Emit PolicyEvents describing their activity
- Control plane collects events via callback, stores in-memory
- Gateway code simplified (no more unwrapping PolicyResult)

## Recently Completed (2025-10-17)

### Activity Stream Integration ✅

Complete real-time activity monitoring for V2 gateway:

1. **Event Models** (`src/luthien_proxy/v2/activity/events.py`)
   - `OriginalRequestReceived` - Tracks incoming client requests
   - `FinalRequestSent` - Tracks requests sent to backend LLM
   - `OriginalResponseReceived` - Tracks non-streaming responses from backend
   - `FinalResponseSent` - Tracks responses sent to client
   - `PolicyEventEmitted` - Tracks policy execution events (already wired in control plane)
   - `OriginalResponseChunk` / `FinalResponseChunk` - Streaming support (TODO)

2. **Activity Publisher** (`src/luthien_proxy/v2/activity/publisher.py`)
   - Publishes events to Redis pub/sub channel `luthien:v2:activity`
   - Graceful degradation when Redis unavailable
   - JSON serialization of Pydantic event models

3. **SSE Stream Handler** (`src/luthien_proxy/v2/activity/stream.py`)
   - Server-Sent Events endpoint at `/v2/activity/stream`
   - 15-second heartbeat to keep connections alive
   - Proper error handling and cleanup

4. **Main Gateway Integration** (`src/luthien_proxy/v2/main.py`)
   - Redis initialization in lifespan manager (with localhost fallback)
   - Activity publisher instantiated on startup
   - Lifecycle events published in OpenAI endpoint:
     - Request received → Policy processing → Request sent → Response received → Policy processing → Response sent
   - Static file serving for UI at `/v2/static`

5. **Activity Monitor UI** (`src/luthien_proxy/v2/static/activity_monitor.html`)
   - Beautiful dark-themed real-time event viewer
   - Automatic SSE connection with reconnection logic
   - Event-type-specific rendering (request/response/policy events)
   - Color-coded by event type
   - Accessible at `/v2/activity/monitor`

**Testing Results:**
- ✅ Events published to Redis successfully
- ✅ SSE stream delivers events with heartbeats
- ✅ UI displays events in real-time
- ✅ Full request/response lifecycle tracked with call_id correlation
- ✅ Policy events published automatically via control plane

**TODO for Activity Stream:**
- Add lifecycle event publishing to Anthropic endpoint (mirror OpenAI)
- Add streaming chunk events (`OriginalResponseChunk`, `FinalResponseChunk`)
- Track policy modifications (populate `modifications` field)
- Add filtering/search to UI (by call_id, event type, etc.)

## What's Next

### Immediate Priorities

1. **Database logging** - Integrate Prisma/PostgreSQL for debug logs
2. **Port one policy** - Migrate a real policy (e.g., `SQLProtectionPolicy`) to v2 interface
3. **Add streaming chunk events** - Track streaming responses in activity stream
4. **Anthropic endpoint events** - Add same lifecycle publishing as OpenAI endpoint

### Medium-term

1. **UI Integration**
   - Port activity stream UI
   - Port debug/trace UI
   - Add static file serving

2. **Policy Migration**
   - Port remaining policies from v1
   - Test each policy independently
   - Document policy migration guide

3. **Docker Setup**
   - Create docker-compose for v2
   - Environment variable configuration
   - Database migrations

### Long-term

1. **Testing**
   - Unit tests for policies
   - Integration tests for endpoints
   - End-to-end tests for full flows
   - Performance benchmarks vs v1

2. **Documentation**
   - User guide for writing policies
   - API reference
   - Migration guide from v1
   - Deployment guide

## Key Design Decisions

### Why Protocol-based interface?

- Allows local and networked implementations without changing gateway code
- Makes testing easier (can mock the interface)
- Clear contract between layers
- Can validate design with local implementation first

### Why separate PolicyHandler from ControlPlaneService?

- PolicyHandler is user-facing (what developers implement)
- ControlPlaneService is system-level (what gateway uses)
- Adapter pattern keeps concerns separate
- PolicyHandler can evolve without breaking ControlPlaneService

### Why not use LiteLLM's proxy server?

- Need control over request/response lifecycle
- Easier to integrate activity streaming
- Simpler deployment story
- More flexibility in policy implementation

## Technical Notes

### LiteLLM Type Issues

- LiteLLM's ModelResponse type annotations are incomplete
- Using `Any` type alias for ModelResponse in TYPE_CHECKING blocks
- Type ignores used for streaming (`async for chunk in response`)
- Type ignores used for response handling (union with CustomStreamWrapper)

### Streaming Architecture

- Bidirectional control: upstream reads from LLM, downstream yields to client
- Policies can emit 0, 1, or many chunks per incoming chunk
- Queue-based communication between policy and gateway
- StreamControl object allows policies to abort or switch models

### Format Conversion

- All internal processing uses OpenAI format
- Anthropic endpoints convert at edges (request → OpenAI, OpenAI → response)
- Streaming chunks converted on-the-fly
- Keeps policy code format-agnostic

## Known Limitations

1. **No database integration yet** - logging is stubbed out
2. **No Redis integration yet** - activity publishing is stubbed out
3. **No UI yet** - just API endpoints
4. **Basic auth only** - using simple API key, no user management
5. **No rate limiting** - policies can implement it, but no built-in support
6. **No metrics** - no Prometheus/StatsD integration yet

## Testing Plan

1. **Manual testing**
   - Start proxy: `uv run python -m luthien_proxy.v2.main`
   - Run test script: `./scripts/test_v2_proxy.py`
   - Verify OpenAI endpoint works
   - Verify Anthropic endpoint works
   - Verify streaming works
   - Verify policies apply

2. **Unit tests**
   - Test PolicyHandler implementations
   - Test format converters
   - Test ControlPlaneLocal

3. **Integration tests**
   - Test endpoints with mock LiteLLM
   - Test policy execution
   - Test error handling

4. **E2E tests**
   - Test against real LLM providers
   - Test streaming with policies
   - Test error recovery
