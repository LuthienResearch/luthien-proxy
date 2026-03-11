# policy_core — Neutral Contract Layer

## OVERVIEW

Interfaces, contexts, and builders that define the policy system. Most-imported package — depended on by `policies`, `streaming`, `orchestration`, `pipeline`. Never depends on them.

## STRUCTURE

```
policy_core/
├── openai_interface.py              # OpenAIPolicyInterface ABC (10 abstract hooks)
├── anthropic_execution_interface.py # AnthropicExecutionInterface Protocol (single method)
├── base_policy.py                   # BasePolicy: config serialization + mutable-state validation
├── policy_protocol.py               # PolicyProtocol (legacy runtime-checkable Protocol)
├── policy_context.py                # PolicyContext: request-scoped state + typed policy state API
├── streaming_policy_context.py      # StreamingPolicyContext: wraps PolicyContext + egress_queue + StreamState
├── chunk_builders.py                # create_text_chunk, create_finish_chunk, create_tool_call_chunk, etc.
├── response_utils.py                # extract_tool_calls_from_response
└── streaming_utils.py               # send_text, send_chunk, send_tool_call, get_last_ingress_chunk
```

## TWO API MODELS

**OpenAI path** (hook-based): Framework owns execution, calls policy hooks at each streaming event.

```
on_openai_request → [backend call] → on_chunk_received → on_content_delta →
  on_content_complete → on_tool_call_delta → on_tool_call_complete →
  on_finish_reason → on_stream_complete → on_streaming_policy_complete (cleanup only)
```

**Anthropic path** (execution-oriented): Policy owns the entire request lifecycle.

```python
async def run_anthropic(self, io, context) -> AsyncIterator[AnthropicPolicyEmission]:
    # Policy calls io.complete() or io.stream() zero or more times
    # Emits events/responses as it goes
```

## WHERE TO LOOK

| Task | File | Notes |
|------|------|-------|
| Implement OpenAI policy hooks | `openai_interface.py` | 10 abstract methods, well-documented |
| Implement Anthropic policy | `anthropic_execution_interface.py` | Single `run_anthropic` method |
| Access request-scoped state | `policy_context.py` | `get_policy_state()` / `pop_policy_state()` |
| Build streaming chunks | `chunk_builders.py` | `create_text_chunk`, `create_finish_chunk` |
| Helper functions for policies | `streaming_utils.py` | `send_text`, `send_chunk` shortcuts |
| Understand StreamingPolicyContext | `streaming_policy_context.py` | Wraps PolicyContext + egress_queue + StreamState |

## KEY TYPES

| Type | Purpose |
|------|---------|
| `PolicyContext` | Request-scoped: `transaction_id`, `request`, `scratchpad`, `emitter`, typed state API |
| `StreamingPolicyContext` | Adds `egress_queue` (where policies push chunks), `stream_state`, `keepalive` |
| `ContentStreamBlock` | Accumulated text content from streaming |
| `ToolCallStreamBlock` | Accumulated function name + JSON arguments |
| `StreamState` | Tracks all blocks, current_block, finish_reason, raw_chunks |

## CONVENTIONS

- **`PolicyContext` is mutable, `ObservabilityContext` is immutable** — clear separation of concerns.
- **Typed policy state**: `ctx.get_policy_state(self, MyStateType, MyStateType)` — keyed by `(policy instance, state type)`. Framework-owned, not policy-owned.
- **`on_streaming_policy_complete`**: Always-run cleanup hook (even on errors). Must NOT emit chunks or modify responses.
- **`on_stream_complete`**: Normal completion — safe to emit final chunks here.
- **`policy_protocol.py` is legacy** — new code should use `OpenAIPolicyInterface`. Protocol kept for runtime checks in infrastructure code.
