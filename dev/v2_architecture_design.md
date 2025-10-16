# V2 Architecture Design: Network-Ready Control Plane

## Overview

This document describes the integrated architecture that replaces LiteLLM proxy + separate control plane with a unified service that uses LiteLLM as a library. The design maintains a clean interface allowing the control plane logic to be separated and networked in the future.

## Core Design Principle: Interface Segregation

The architecture separates three concerns:

1. **API Gateway** (FastAPI web layer)
   - Handles HTTP/SSE endpoints
   - Authentication/authorization
   - Request/response format conversion (OpenAI ↔ Anthropic)
   - UI serving (activity stream, debug interfaces)

2. **Control Logic** (policy execution layer)
   - Policy application (request/response/streaming)
   - Decision making and validation
   - Database logging
   - Activity publishing

3. **LLM Integration** (LiteLLM library)
   - Multi-provider LLM calls
   - Format normalization
   - Token counting

## Network-Ready Interface

### ControlPlaneService Interface

The control logic is accessed through a protocol-agnostic interface:

```python
class ControlPlaneService(Protocol):
    """Interface for control plane operations.

    This can be implemented as:
    - In-process calls (ControlPlaneLocal)
    - HTTP client (ControlPlaneHTTP)
    - gRPC client (ControlPlaneGRPC)
    - etc.
    """

    async def apply_request_policies(
        self,
        request_data: dict,
        metadata: RequestMetadata
    ) -> PolicyResult[dict]:
        """Apply policies to incoming request before LLM call."""
        ...

    async def apply_response_policy(
        self,
        response: ModelResponse,
        metadata: RequestMetadata
    ) -> PolicyResult[ModelResponse]:
        """Apply policies to complete response after LLM call."""
        ...

    async def create_streaming_context(
        self,
        request_data: dict,
        metadata: RequestMetadata
    ) -> StreamingContext:
        """Initialize streaming context and return stream ID."""
        ...

    async def process_streaming_chunk(
        self,
        chunk: ModelResponse,
        context: StreamingContext
    ) -> AsyncIterator[PolicyResult[ModelResponse]]:
        """Process a streaming chunk through policies."""
        ...

    async def publish_activity(
        self,
        event: ActivityEvent
    ) -> None:
        """Publish activity event for UI consumption."""
        ...

    async def log_debug_event(
        self,
        debug_type: str,
        payload: JSONObject
    ) -> None:
        """Log debug event to database."""
        ...
```

### Data Models

```python
@dataclass
class RequestMetadata:
    """Metadata about the request context."""
    call_id: str
    trace_id: Optional[str]
    user_id: Optional[str]
    api_key_hash: str
    timestamp: datetime

@dataclass
class PolicyResult[T]:
    """Result of policy application."""
    value: T
    allowed: bool
    reason: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass
class StreamingContext:
    """Context for streaming operations."""
    stream_id: str
    call_id: str
    request_data: dict
    policy_state: dict[str, Any]
```

## Implementation Strategy

### Phase 1: Local Implementation (Current Objective)

Implement `ControlPlaneLocal` that runs in-process:

```python
class ControlPlaneLocal:
    """In-process implementation of control plane service."""

    def __init__(
        self,
        policy: PolicyHandler,
        db_pool: DatabasePool,
        redis_client: RedisClient,
    ):
        self.policy = policy
        self.db_pool = db_pool
        self.redis_client = redis_client

    async def apply_request_policies(
        self,
        request_data: dict,
        metadata: RequestMetadata
    ) -> PolicyResult[dict]:
        # Direct function call to policy
        result = await self.policy.apply_request_policies(request_data)

        # Log to database
        await self.log_debug_event("request_policy", {...})

        # Publish to activity stream
        await self.publish_activity(ActivityEvent(...))

        return PolicyResult(value=result, allowed=True)
```

### Phase 2: Network Implementation (Future)

Later, implement `ControlPlaneHTTP` that makes network calls:

```python
class ControlPlaneHTTP:
    """HTTP client implementation of control plane service."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key

    async def apply_request_policies(
        self,
        request_data: dict,
        metadata: RequestMetadata
    ) -> PolicyResult[dict]:
        # HTTP POST to control plane service
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/v1/policies/request",
                json={
                    "request_data": request_data,
                    "metadata": metadata.to_dict(),
                },
                headers={"Authorization": f"Bearer {self.api_key}"}
            )
            return PolicyResult.from_dict(response.json())
```

The API gateway code doesn't change - it just uses a different implementation of the interface.

## Directory Structure

```
src/luthien_proxy/v2/
├── __init__.py
├── main.py                    # FastAPI app + startup
├── api/
│   ├── __init__.py
│   ├── chat.py                # OpenAI /v1/chat/completions
│   ├── messages.py            # Anthropic /v1/messages
│   ├── activity.py            # Activity stream SSE
│   └── debug.py               # Debug UI endpoints
├── control/
│   ├── __init__.py
│   ├── interface.py           # ControlPlaneService protocol
│   ├── local.py               # ControlPlaneLocal implementation
│   ├── models.py              # RequestMetadata, PolicyResult, etc.
│   └── policy_adapter.py      # Adapts PolicyHandler to interface
├── policies/
│   ├── __init__.py
│   ├── base.py                # PolicyHandler abstract base
│   ├── noop.py                # NoOpPolicy
│   └── ...                    # Port existing policies
├── llm/
│   ├── __init__.py
│   ├── client.py              # LiteLLM wrapper
│   └── format_converters.py  # OpenAI ↔ Anthropic conversion
└── ui/
    ├── templates/             # Jinja2 templates
    └── static/                # CSS/JS assets
```

## Migration Path

1. **Phase 1: Core proxy + one policy** (Current objective)
   - Implement `ControlPlaneService` protocol
   - Implement `ControlPlaneLocal`
   - Create `PolicyHandler` base class
   - Port `NoOpPolicy` to new interface
   - Wire up OpenAI endpoint
   - Basic activity stream

2. **Phase 2: Feature parity**
   - Port remaining policies
   - Add Anthropic endpoint
   - Full activity stream UI
   - Debug UI
   - Database logging
   - Redis caching

3. **Phase 3: Testing and docs**
   - Unit tests for all policies
   - Integration tests for endpoints
   - End-to-end tests
   - Migration guide
   - Performance comparison

4. **Phase 4: Optional network separation** (future, out of scope)
   - Implement `ControlPlaneHTTP`
   - Create standalone control plane service
   - Benchmark network overhead
   - Document deployment options

## Benefits

### Immediate (V2)
- Simpler deployment (one process vs two)
- Lower latency (no network hop for policies)
- Easier debugging (single process)
- Cleaner code (direct library usage vs hooks)

### Future (network separation)
- Scale control plane independently
- Multiple gateways → single control plane
- Shared policy state across gateways
- Centralized audit logging
- Policy updates without gateway restart

## Trade-offs

### V2 (integrated)
- ✅ Simpler for small/medium deployments
- ✅ Lower latency
- ✅ Easier to develop/debug
- ❌ Control plane scales with gateway
- ❌ Must restart gateway to update policies

### V3 (networked)
- ✅ Independent scaling
- ✅ Shared state
- ✅ Hot policy updates
- ❌ Network latency overhead
- ❌ More complex deployment
- ❌ Need to handle network failures

## Design Decisions

### Why Protocol-based interface?
- Allows multiple implementations without code changes
- Makes testing easier (can mock the interface)
- Clear contract between layers
- Python's Protocol provides structural typing

### Why not just expose HTTP endpoints now?
- YAGNI: Don't build network layer until we need it
- Easier to add later than to maintain early
- Can validate interface design with local implementation first
- Network layer adds complexity (retries, timeouts, auth, etc.)

### Why keep PolicyHandler separate from ControlPlaneService?
- PolicyHandler is the user-facing abstraction (what developers implement)
- ControlPlaneService is the system-level interface (what the gateway uses)
- Adapter pattern keeps concerns separate
- Allows PolicyHandler to evolve without breaking ControlPlaneService

### Why not use LiteLLM's proxy server?
- We need control over the request/response lifecycle
- Easier to integrate activity streaming
- Simpler deployment story
- More flexibility in policy implementation
- LiteLLM proxy has features we don't need (and complexity we don't want)

## Next Steps

See [dev/OBJECTIVE.md](OBJECTIVE.md) for the current implementation plan.
