# V2 Architecture Implementation Notes

## What's Been Done

### Core Architecture (Completed)

1. **Control Plane Interface** (`src/luthien_proxy/v2/control/`)
   - `interface.py`: Protocol definition for `ControlPlaneService`
   - `models.py`: Data models (`RequestMetadata`, `PolicyResult`, `StreamingContext`)
   - `local.py`: In-process implementation (`ControlPlaneLocal`)
   - Clean separation allows future network implementation without changing gateway code

2. **Policy Abstraction** (`src/luthien_proxy/v2/policies/`)
   - `base.py`: `PolicyHandler` abstract base class with streaming support
   - `DefaultPolicyHandler`: Example implementation with token limits, content filtering
   - `noop.py`: `NoOpPolicy` for testing and baseline
   - Simple interface: `apply_request_policies`, `apply_response_policy`, `apply_streaming_chunk_policy`

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

### Type Safety

- All type checks passing with pyright
- LiteLLM's incomplete type annotations handled with `Any` type aliases
- Type ignores used strategically for streaming and response handling

## What's Next

### Immediate Priorities

1. **Test the basic proxy** - Start it up and verify OpenAI/Anthropic endpoints work
2. **Activity stream integration** - Port activity publishing from v1
3. **Database logging** - Integrate Prisma/PostgreSQL for debug logs
4. **Port one policy** - Migrate `NoOpPolicy` or `SQLProtectionPolicy` to v2 interface

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
