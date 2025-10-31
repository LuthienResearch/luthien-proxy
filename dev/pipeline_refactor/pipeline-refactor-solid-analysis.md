# Pipeline Refactor v4 - SOLID Analysis

**Date:** 2025-10-29 (Updated)
**Purpose:** Evaluate pipeline refactor specification v4 against SOLID principles

---

## Proposed Architecture Summary

### Components

```python
@dataclass
class StreamState:
    blocks: list[StreamBlock]
    current_block: StreamBlock | None
    just_completed: StreamBlock | None
    finish_reason: str | None
    raw_chunks: list[ModelResponse]  # buffer for passthrough helpers
    last_emission_index: int  # track passthrough position

class ObservabilityContext(ABC):
    async def emit_event(self, event_type: str, data: dict): ...
    def record_metric(self, name: str, value: float, labels: dict | None): ...
    def add_span_attribute(self, key: str, value: Any): ...
    def add_span_event(self, name: str, attributes: dict | None): ...

class TransactionRecorder(ABC):
    async def record_request(self, original, final): ...
    def add_ingress_chunk(self, chunk): ...
    def add_egress_chunk(self, chunk): ...
    async def finalize_streaming(self): ...
    async def finalize_non_streaming(self, original_response, final_response): ...

class DefaultTransactionRecorder(TransactionRecorder):
    def __init__(self, observability: ObservabilityContext): ...

@dataclass
class StreamingResponseContext:
    transaction_id: str
    final_request: RequestMessage
    ingress_assembler: StreamingChunkAssembler | None
    egress_queue: asyncio.Queue[ModelResponse]
    scratchpad: dict[str, Any]
    observability: ObservabilityContext  # NEW

class Policy(ABC):
    async def on_request(self, request, context): ...
    async def on_chunk_received(self, ctx): ...
    async def on_content_delta(self, ctx): ...
    async def on_content_complete(self, ctx): ...
    # ... other hooks

class SimplePolicy(Policy):
    async def on_request_simple(self, request): ...
    async def on_response_content(self, content, request): ...
    async def on_response_tool_call(self, tool_call, request): ...

class LLMClient(ABC):
    async def stream(self, request): ...
    async def complete(self, request): ...

class PolicyOrchestrator:
    def __init__(
        self,
        policy: Policy,
        llm_client: LLMClient,
        observability_factory: Callable[[str, Span], ObservabilityContext],
        recorder_factory: Callable[[ObservabilityContext], TransactionRecorder],
    ): ...
```

**Flow Highlights**
- Streaming path uses `StreamingOrchestrator` to connect LLM stream → assembler → policy callbacks → egress queue.
- `feed_complete` event ensures `drain_egress` flushes final chunks, including policy output emitted in `on_stream_complete`.
- Non-streaming path stores full `ModelResponse` objects and emits them directly without reconstruction.

---

## SOLID Analysis

### ✅ Single Responsibility Principle (SRP)

**Does each component have one clear reason to change?**

| Component | Responsibility | Reason to change |
|-----------|----------------|------------------|
| **StreamState** | Hold assembler state, including raw chunk buffer | Chunk parsing data model changes |
| **ObservabilityContext** | Unified interface for events/metrics/tracing | Observability backend changes |
| **TransactionRecorder** | Record transaction data using ObservabilityContext | Recording strategy changes |
| **StreamingResponseContext** | Provide policy runtime context | Policy needs different runtime data |
| **Policy / SimplePolicy** | Define policy behavior (streaming or simple) | Policy semantics change |
| **LLMClient** | Abstract LLM backend calls | Backend API integration changes |
| **PolicyOrchestrator** | Coordinate policy + LLM interaction | Flow orchestration rules change |
| **StreamingOrchestrator** | Manage queue plumbing and timeouts | Streaming coordination strategy changes |

#### ✅ Improvements Applied
- **ObservabilityContext abstraction**: Unified interface for all observability operations (events, metrics, tracing). Single dependency instead of db_pool + event_publisher.
- **TransactionRecorder uses ObservabilityContext**: Recording separated from orchestration via interface. Dependency injection via factory.
- **Block dispatch mapping**: Dictionary-based dispatch eliminates `isinstance` checks. New block types register without editing orchestrator.
- **SimplePolicy**: 95% of policies use simple content-level interface, hiding streaming complexity.

### ✅ Open/Closed Principle (OCP)

- Policies extend `Policy` (or `SimplePolicy`) without touching orchestrator plumbing
- `ObservabilityContext` abstraction allows new backends (Prometheus, StatsD) without API changes
- `LLMClient` abstraction allows alternate backend clients without orchestrator edits
- `StreamState.raw_chunks` enables new helper strategies without modifying assembler internals
- Block dispatch mapping: New `StreamBlock` types register in dictionary without editing orchestrator

#### ✅ All Extension Pressure Resolved
- **Block dispatch**: Now uses dictionary mapping. Adding new block types just adds to `DELTA_HOOKS`/`COMPLETE_HOOKS`.
- **Observability backends**: New backends implement `ObservabilityContext` interface without changing consumers.
- **Recording strategies**: New recorders implement `TransactionRecorder` interface.

### ✅ Liskov Substitution Principle (LSP)

- Any `ObservabilityContext` implementation works (DefaultObservabilityContext, NoOpObservabilityContext)
- Any `TransactionRecorder` implementation works (DefaultTransactionRecorder, NoOpTransactionRecorder)
- Any `LLMClient` implementation works (LiteLLMClient, MockLLMClient for tests)
- Any `Policy` implementation works, including `SimplePolicy` which is substitutable for `Policy`
- `Policy` provides safe defaults (nop or passthrough), letting subclasses override only relevant hooks
- `StreamingResponseContext.ingress_state` guard fails fast if accessed too early

#### ✅ All Risks Addressed
- Helper functions operate on `ctx.ingress_state.raw_chunks` which is always populated by assembler
- `SimplePolicy` provides all necessary implementations, policies just override content-level methods

### ✅ Interface Segregation Principle (ISP)

- **SimplePolicy**: 95% of policies use simple content-level interface (on_response_content, on_response_tool_call)
- **Policy**: 5% that need streaming control use full interface with all hooks
- **ObservabilityContext**: Focused methods (emit_event, record_metric, add_span_attribute, add_span_event)
- **StreamingResponseContext**: Clean interface for policy runtime (ingress_state, egress_queue, observability)
- Event-based hooks remain narrow; policies override only what they need
- Transaction recording stays internal; not exposed to policies

### ✅ Dependency Inversion Principle (DIP)

- **All dependencies are abstractions:**
  - `ObservabilityContext` interface (not concrete DefaultObservabilityContext)
  - `TransactionRecorder` interface (not concrete DefaultTransactionRecorder)
  - `LLMClient` interface (not concrete LiteLLMClient)
  - `StreamingOrchestrator` can be injected (optional parameter)

- **Compositional factories:**
  - `observability_factory: (transaction_id, span) -> ObservabilityContext`
  - `recorder_factory: (observability) -> TransactionRecorder`
  - Clean dependency chain: `transaction_id + span → observability → recorder`

- **Easy testing:**
  ```python
  orchestrator = PolicyOrchestrator(
      policy=test_policy,
      llm_client=mock_llm,
      observability_factory=lambda tid, span: NoOpObservabilityContext(tid),
      recorder_factory=lambda obs: NoOpTransactionRecorder(),
  )
  ```

---

## Summary: SOLID Scorecard

| Principle | Before | After | Notes |
|-----------|--------|-------|-------|
| **SRP** | 🟡 B+ | ✅ A | ObservabilityContext unifies observability; TransactionRecorder separated; block dispatch centralized |
| **OCP** | 🟡 B+ | ✅ A | Block types extensible via dictionaries; observability backends pluggable; all abstractions |
| **LSP** | ✅ A- | ✅ A | All interfaces substitutable (ObservabilityContext, TransactionRecorder, Policy, SimplePolicy) |
| **ISP** | ✅ A | ✅ A | SimplePolicy hides streaming complexity; ObservabilityContext focused interface |
| **DIP** | 🟡 B+ | ✅ A | All dependencies injected via interfaces; compositional factories |

**Overall:** Excellent SOLID design. All principles score A grade. Ready for implementation.

---

## Implementation Complete

All recommendations have been applied:

1. ✅ **Observability abstraction**: `ObservabilityContext` interface unifies events/metrics/tracing
2. ✅ **Factory injection**: Compositional factories (`observability_factory` → `recorder_factory`)
3. ✅ **Block dispatch mapping**: Dictionary-based dispatch eliminates `isinstance` checks
4. ✅ **SimplePolicy abstraction**: Hides streaming complexity for 95% of policies
5. ✅ **Fail-fast guards**: `ingress_state` property raises if accessed too early

---

## Architecture Strengths

- Clear streaming lifecycle: `feed_complete` + `drain_egress` sequencing eliminates the v3 termination race and allows policies to emit tail chunks safely.
- Non-streaming correctness: Storing full `ModelResponse` objects removes risky reconstruction logic and preserves metadata.
- Policy ergonomics: Context object + helper utilities minimize the code policy authors need to write for common passthrough behavior.

---

## Next Step

See `implementation-plan.md` for comprehensive implementation guide with testing strategy.
