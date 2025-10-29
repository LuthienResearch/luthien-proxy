# Pipeline Architecture - SOLID Analysis

**Date:** 2025-10-28
**Purpose:** Analyze proposed pipeline architecture against SOLID principles

---

## Proposed Architecture Summary

### Components

```python
# Data + Logic (PolicyOrchestrator owns this)
class TransactionRecord:
    transaction_id: str
    original_request: RequestMessage
    final_request: RequestMessage | None
    original_response: ModelResponse | None
    final_response: ModelResponse | None

    async def record_request(self, original, final):
        """Update and emit to DB/Redis"""

    async def record_response(self, original, final):
        """Update and emit to DB/Redis"""

# Parsing chunks → blocks + raw_chunks
class StreamingChunkAssembler:
    state: StreamState  # blocks, current_block, just_completed, raw_chunks

    async def process(self, chunk: ModelResponse):
        """Parse chunk, update state"""

# Event coordination via queues
class StreamingResponse:
    assembler: StreamingChunkAssembler
    queue: asyncio.Queue  # Enqueues events
    timeout_tracker: TimeoutTracker

    async def add_chunk(self, chunk):
        """Add chunk, enqueue events (chunk_received, content_delta, etc.)"""

    async def mark_complete(self):
        """Enqueue stream_complete event"""

# Context passed to policy
@dataclass
class StreamingResponseContext:
    transaction_id: str
    final_request: RequestMessage
    ingress_state: StreamState
    egress_state: StreamState
    scratchpad: dict
    span: Span

# Orchestrates the whole flow
class PolicyOrchestrator:
    policy: LuthienPolicy
    llm_client: LLMClient
    transaction_record: TransactionRecord

    async def process_request(self, request) -> RequestMessage:
        """Apply policy to request, record original+final"""

    async def process_streaming_response(self, request) -> AsyncIterator:
        """
        - Create ingress + egress StreamingResponse
        - Feed LLM chunks → ingress
        - Process ingress events → call policy → egress
        - Return egress stream
        """

# Abstracts LLM backend calls
class LLMClient:
    async def stream(self, request: RequestMessage) -> AsyncIterator[ModelResponse]:
        """Call litellm.acompletion, return OpenAI format chunks"""

    async def complete(self, request: RequestMessage) -> ModelResponse:
        """Call litellm.acompletion (non-streaming)"""

# Utility functions (not methods)
async def send_text(ctx: StreamingResponseContext, text: str, egress: StreamingResponse):
    """Helper to push text chunk to egress"""

async def send_chunk(ctx: StreamingResponseContext, chunk: ModelResponse, egress: StreamingResponse):
    """Helper to push chunk to egress"""
```

---

## SOLID Analysis

### ✅ Single Responsibility Principle (SRP)

**Does each component have one clear reason to change?**

| Component | Responsibility | Reason to Change |
|-----------|---------------|------------------|
| **TransactionRecord** | Store + emit transaction data | DB schema or event format changes |
| **StreamingChunkAssembler** | Parse chunks into blocks | Chunk parsing logic changes |
| **StreamingResponse** | Queue events as chunks arrive | Event coordination mechanism changes |
| **StreamingResponseContext** | Hold context for policy invocation | Policy needs different context |
| **PolicyOrchestrator** | Coordinate flow between components | Flow orchestration changes |
| **LLMClient** | Call LLM backend | LLM provider API changes |
| **Policy** | Make decisions about requests/responses | Business logic changes |
| **Gateway** | HTTP request/response handling | HTTP protocol changes |

#### 🟡 Potential SRP Violations

**1. PolicyOrchestrator owns TransactionRecord**

You said: "PolicyOrchestrator owns TransactionRecord"

**Concern:** PolicyOrchestrator now has TWO reasons to change:
1. Flow orchestration logic changes
2. Transaction recording needs change

**Current responsibilities in PolicyOrchestrator:**
- Create ingress/egress StreamingResponse
- Wire up event processing
- Call policy methods
- **Record transactions** ← Mixed responsibility

**Is this a violation?**

**Argument FOR combining:**
- Recording is integral to orchestration flow
- PolicyOrchestrator knows when to record (after policy decisions)
- Simpler than passing recorder around

**Argument AGAINST combining:**
- Recording is observability, orchestration is control flow
- If we change recording strategy (e.g., batch vs real-time), orchestrator changes
- Harder to test orchestration without DB dependencies

**Recommendation:** 🟡 **Borderline acceptable**
- If TransactionRecord is just a data object with simple methods, OK
- If recording logic becomes complex (batching, retry, etc.), extract to separate `TransactionRecorder` that orchestrator uses

**2. StreamingResponse - Event queuing + timeout tracking**

```python
class StreamingResponse:
    assembler: StreamingChunkAssembler
    queue: asyncio.Queue
    timeout_tracker: TimeoutTracker
```

**Two responsibilities:**
1. Coordinate events (queuing)
2. Track timeouts

**Is this a violation?**

**Argument FOR combining:**
- Timeout is part of streaming lifecycle
- Tightly coupled - timeout needs access to queue activity

**Argument AGAINST:**
- Could extract timeout to separate component
- StreamingResponse changes if either queuing or timeout logic changes

**Recommendation:** ✅ **Acceptable**
- Timeout tracking is a cross-cutting concern for streaming
- Using TimeoutTracker (separate class) already provides separation
- Low risk

**3. Gateway - HTTP + format conversion**

```python
# Gateway converts client format → OpenAI
openai_request = anthropic_to_openai_request(data)

# Gateway converts OpenAI → client format
client_stream = convert_to_client_format(egress_stream)
```

**Two responsibilities:**
1. HTTP handling
2. Format conversion

**Is this a violation?**

**Argument FOR combining:**
- Format conversion is specific to the HTTP endpoint
- Anthropic endpoint needs Anthropic conversion
- OpenAI endpoint needs no conversion
- Tightly coupled to request/response cycle

**Argument AGAINST:**
- Could extract to FormatConverter middleware
- Gateway changes if either HTTP framework or format logic changes

**Recommendation:** ✅ **Acceptable for now**
- Format conversion is thin (just function calls)
- Specific to endpoint (not generic middleware)
- Could extract later if conversion becomes complex

---

### ✅ Open/Closed Principle (OCP)

**Can we extend behavior without modifying existing code?**

#### Good Examples

**1. New policy types**
```python
# Add new policy without changing PolicyOrchestrator
class CachingPolicy(EventBasedPolicy):
    async def on_request(self, request, context):
        # Check cache...
```
✅ PolicyOrchestrator doesn't change

**2. New event types**
```python
# StreamingResponse could fire new events
await self.queue.put(("thinking_delta", state))
```
✅ Just add new event handler in PolicyOrchestrator._process_events

**3. Different LLM backends**
```python
class AnthropicDirectClient(LLMClient):
    async def stream(self, request):
        # Call Anthropic API directly
```
✅ LLMClient interface allows swapping implementations

#### 🔴 Potential Violations

**1. Adding new chunk types**

If OpenAI adds new chunk types (e.g., `ReasoningBlock`), we'd need to modify:
- StreamingChunkAssembler parsing logic
- StreamState to hold new block type
- Policy interface (new on_reasoning_complete hook?)

**Is this acceptable?**

✅ **Yes** - this is a fundamental schema change, reasonable to modify code
- Could mitigate with plugin system for parsers
- Probably overkill for our use case

**2. Different event coordination mechanisms**

Currently uses queues. What if we want:
- RxJS-style observables?
- Direct event firing (no queue)?
- Actor model?

Would need to rewrite StreamingResponse.

**Is this acceptable?**

✅ **Yes** - changing coordination mechanism is a major architectural shift
- Queue-based is flexible enough for foreseeable needs
- OK to require code changes for this

---

### ✅ Liskov Substitution Principle (LSP)

**Can we substitute implementations without breaking behavior?**

#### Interface Points

**1. Policy interface**
```python
class LuthienPolicy:
    async def on_request(self, request, context) -> RequestMessage: ...
    async def on_chunk_received(self, ctx: StreamingResponseContext): ...
    # etc.
```

✅ Any policy implementation should work with PolicyOrchestrator
- EventBasedPolicy, NoOpPolicy, CachingPolicy all substitutable
- No violations expected

**2. LLMClient interface**
```python
class LLMClient:
    async def stream(self, request) -> AsyncIterator[ModelResponse]: ...
    async def complete(self, request) -> ModelResponse: ...
```

✅ Different LLM backends should return same types
- **Precondition:** Must return OpenAI-format ModelResponse
- **Postcondition:** Chunks must be parseable by StreamingChunkAssembler

**Potential issue:** What if Anthropic direct API returns different chunk structure?

**Mitigation:** LLMClient is responsible for normalizing to OpenAI format
- If using anthropic SDK directly, convert in LLMClient
- litellm already handles this

**3. StreamingResponse**

Not currently an interface (just one implementation). If we needed multiple:
```python
class StreamingResponse(ABC):
    @abstractmethod
    async def add_chunk(self, chunk): ...
```

Could have:
- QueueBasedStreamingResponse (current)
- DirectCallbackStreamingResponse
- ActorBasedStreamingResponse

✅ Currently no LSP concerns (only one implementation)

---

### ✅ Interface Segregation Principle (ISP)

**Are interfaces focused, or do clients depend on methods they don't use?**

#### Policy Interface

Current EventBasedPolicy has ~10 methods:
```python
on_request
on_response
on_stream_start
on_chunk_received
on_content_delta
on_content_complete
on_tool_call_delta
on_tool_call_complete
on_finish_reason
on_stream_complete
```

**Concern:** Most policies don't implement all methods. Is this a violation?

**Mitigations:**
1. ✅ All methods have default implementations (no-op or passthrough)
2. ✅ Policies only override what they need
3. ✅ Methods are cohesive (all related to streaming response processing)

**Verdict:** ✅ **Not a violation**
- This is the Template Method pattern
- Optional methods via defaults is acceptable

**Alternative (if this becomes a problem):**
```python
class RequestPolicy(ABC):
    @abstractmethod
    async def on_request(self, request, context): ...

class StreamingPolicy(ABC):
    @abstractmethod
    async def on_chunk_received(self, ctx): ...
    # ... only streaming methods

class FullPolicy(RequestPolicy, StreamingPolicy):
    # Implement both
```

But this adds complexity without clear benefit.

#### StreamingResponseContext

```python
@dataclass
class StreamingResponseContext:
    transaction_id: str
    final_request: RequestMessage
    ingress_state: StreamState
    egress_state: StreamState
    scratchpad: dict
    span: Span
```

**Do all policies need all fields?**

Likely scenarios:
- Simple passthrough policy: doesn't need span or scratchpad
- Content filter: needs ingress_state, maybe not egress_state directly
- Tool limiter: needs scratchpad for counting

**Is this a violation?**

🟡 **Minor concern**
- Some policies might not use all fields
- But fields are lightweight (just references)
- No major harm

**Verdict:** ✅ **Acceptable**
- Dataclass is just a container, not forcing implementations
- Better to have rich context available than split into multiple types

---

### ✅ Dependency Inversion Principle (DIP)

**Do high-level modules depend on abstractions, not concretions?**

#### Current Dependencies

```
Gateway (high-level)
  ↓ depends on
PolicyOrchestrator (high-level)
  ↓ depends on
LuthienPolicy (abstraction) ✅
LLMClient (abstraction) ✅
TransactionRecord (concrete data) ← Depends on data, not behavior
```

```
PolicyOrchestrator
  ↓ depends on
StreamingResponse (concrete) 🟡
```

**Analysis:**

**1. Policy dependency**
```python
class PolicyOrchestrator:
    def __init__(self, policy: LuthienPolicy):  # ← Abstract interface
```
✅ Good - depends on abstraction

**2. LLMClient dependency**
```python
class PolicyOrchestrator:
    def __init__(self, llm_client: LLMClient):  # ← Should be abstract
```
🟡 LLMClient should be an ABC/Protocol:
```python
class LLMClient(Protocol):
    async def stream(self, request: RequestMessage) -> AsyncIterator[ModelResponse]: ...
    async def complete(self, request: RequestMessage) -> ModelResponse: ...

class LiteLLMClient(LLMClient):
    """Concrete implementation using litellm"""
```
✅ Easy fix

**3. StreamingResponse dependency**
```python
class PolicyOrchestrator:
    async def process_streaming_response(self, ...):
        ingress = StreamingResponse()  # ← Creates concrete class directly
        egress = StreamingResponse()
```

🔴 **Violation** - creates concrete StreamingResponse

**Should be:**
```python
class PolicyOrchestrator:
    def __init__(
        self,
        policy: LuthienPolicy,
        llm_client: LLMClient,
        streaming_response_factory: Callable[[], StreamingResponse]  # ← Factory
    ):
```

**Or simpler (acceptable for single implementation):**
```python
# Just instantiate directly if we only have one implementation
ingress = StreamingResponse()
```

**Verdict:** 🟡 **Minor violation, acceptable**
- Only one StreamingResponse implementation planned
- Can refactor to factory if we add more implementations
- Not critical for now

**4. TransactionRecord dependency**
```python
class PolicyOrchestrator:
    transaction_record: TransactionRecord  # ← Concrete data class
```

✅ **OK** - data classes are fine to depend on concretely
- No behavior to mock/swap
- If recording logic becomes complex, extract to interface

---

## Summary: SOLID Scorecard

| Principle | Grade | Notes |
|-----------|-------|-------|
| **SRP** | 🟡 B+ | Minor concerns: PolicyOrchestrator owns recording, gateway does format conversion |
| **OCP** | ✅ A | Can extend via new policies, events, LLM clients |
| **LSP** | ✅ A | Substitution works for policies and LLM clients |
| **ISP** | ✅ A | Policy interface uses template method pattern appropriately |
| **DIP** | 🟡 B+ | Should make LLMClient abstract, but overall good |

**Overall: Solid B+ / A-**

---

## Recommendations

### Critical (Do Before Implementation)

1. **Make LLMClient an ABC or Protocol**
```python
from abc import ABC, abstractmethod

class LLMClient(ABC):
    @abstractmethod
    async def stream(self, request: RequestMessage) -> AsyncIterator[ModelResponse]:
        """Stream responses from LLM backend."""

    @abstractmethod
    async def complete(self, request: RequestMessage) -> ModelResponse:
        """Get complete response from LLM backend."""

class LiteLLMClient(LLMClient):
    async def stream(self, request: RequestMessage):
        response = await litellm.acompletion(**request.model_dump(), stream=True)
        async for chunk in response:
            yield chunk

    async def complete(self, request: RequestMessage):
        return await litellm.acompletion(**request.model_dump(), stream=False)
```

### Optional (Revisit If Needed)

2. **Consider extracting TransactionRecorder if recording becomes complex**
```python
class TransactionRecorder:
    """Handles recording logic, separate from data"""
    def __init__(self, record: TransactionRecord, db_pool, event_publisher):
        self.record = record
        # ...

class PolicyOrchestrator:
    def __init__(self, policy, llm_client, recorder: TransactionRecorder):
        self.recorder = recorder
```

Only do this if:
- Recording needs batching, retry, or complex error handling
- We want to test orchestration without DB
- Recording strategy varies by deployment

3. **Add StreamingResponse interface if multiple implementations needed**

Only do this if we actually need different implementations (direct callbacks, actors, etc.)

---

## Architecture Strengths

✅ **Clear separation of concerns**
- LLMClient abstracts backend
- StreamingResponse handles event coordination
- Policy contains business logic
- Gateway handles HTTP

✅ **Testable**
- Can mock LLMClient
- Can test policies in isolation
- Can test orchestration with fake components

✅ **Extensible**
- New policies don't change orchestrator
- New LLM backends via LLMClient interface
- New events via queue extension

✅ **Queue-based coordination**
- Handles backpressure
- Enables timeout tracking
- Decouples ingress from egress

---

## Next Steps

1. Implement LLMClient as ABC + LiteLLMClient concrete
2. Implement the architecture as designed
3. Monitor TransactionRecord in PolicyOrchestrator - extract if it grows complex
4. Write tests to validate SOLID principles hold
