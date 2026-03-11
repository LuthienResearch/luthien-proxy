# streaming — Queue-Based Streaming Pipeline

## OVERVIEW

Two-stage pipeline: PolicyExecutor (chunk assembly + policy hooks) → ClientFormatter (SSE conversion). Connected by typed `asyncio.Queue`s.

## STRUCTURE

```
streaming/
├── stream_blocks.py              # StreamBlock, ContentStreamBlock, ToolCallStreamBlock
├── stream_state.py               # StreamState: accumulates blocks, tracks current_block
├── streaming_chunk_assembler.py  # Raw ModelResponse chunks → StreamBlocks (state machine)
├── queue_utils.py                # safe_put helper
├── policy_executor/
│   ├── executor.py               # PolicyExecutor: chunk assembly + hook invocation + timeout
│   └── timeout_monitor.py        # TimeoutMonitor + PolicyTimeoutError
└── client_formatter/
    ├── interface.py              # ClientFormatter Protocol
    └── openai.py                 # OpenAIClientFormatter: ModelResponse → SSE strings
```

## DATA FLOW

```
Backend LLM (AsyncIterator[ModelResponse])
         │
         ▼
   PolicyExecutor
   ├── StreamingChunkAssembler → builds StreamBlocks
   ├── Invokes policy hooks (on_chunk_received, on_content_delta, etc.)
   ├── Policy writes to egress_queue
   ├── Executor drains egress_queue → policy_out_queue
   └── TimeoutMonitor runs in parallel (keepalive-based)
         │
         │ policy_out_queue: Queue[ModelResponse | None]
         ▼
   ClientFormatter
   ├── ModelResponse.model_dump_json()
   ├── Wraps as "data: {json}\n\n"
   └── Appends "data: [DONE]\n\n"
         │
         │ sse_queue: Queue[str | None]
         ▼
   PolicyOrchestrator._drain_queue() → FastAPI StreamingResponse → Client
```

**`None` sentinel** signals end-of-stream through each queue. **`asyncio.TaskGroup`** ensures error propagation — if either stage fails, the other is cancelled.

## WHERE TO LOOK

| Task | File | Notes |
|------|------|-------|
| How chunks become blocks | `streaming_chunk_assembler.py` | State machine: normalizes LiteLLM quirks |
| Policy hook invocation order | `policy_executor/executor.py` | See `_process_chunk()` method |
| Timeout/keepalive behavior | `policy_executor/timeout_monitor.py` | Keepalive called per-chunk + by policies |
| SSE wire format | `client_formatter/openai.py` | `data: {json}\n\n` + `data: [DONE]\n\n` |
| Block types | `stream_blocks.py` | ContentStreamBlock (text), ToolCallStreamBlock (function) |
| Accumulated state | `stream_state.py` | `blocks`, `current_block`, `just_completed`, `finish_reason` |

## KEY PATTERNS

- **Block assembly**: `StreamingChunkAssembler` accumulates raw `ModelResponse` chunks into typed `StreamBlock`s. Detects block boundaries (content → tool_call transitions). `just_completed` flag is set for one chunk, then cleared.
- **Blocks stream sequentially**: content (if any) → tool_call_0 → tool_call_1 → ... → finish.
- **Egress queue**: Policies push transformed chunks to `StreamingPolicyContext.egress_queue`. PolicyExecutor drains it after each hook invocation and forwards to `policy_out_queue`.
- **Bounded queues**: maxsize=10000, 30s timeout on `put()` — acts as circuit breaker.
- **This pipeline is OpenAI-path only**. Anthropic streaming goes through `anthropic_processor.py` — the policy IS the pipeline (no assembler, no queues).

## ANTI-PATTERNS

- **Don't add `None` sentinel checking inside formatters/executors** — `None` is ONLY the end-of-stream signal at queue boundaries.
- **Don't combine content + finish_reason** in one chunk — `convert_chunk_to_event()` returns early on content, never reaching finish_reason check.
- **Don't busy-wait with `get_nowait()` in loops** — block with `await queue.get()` for efficiency.
