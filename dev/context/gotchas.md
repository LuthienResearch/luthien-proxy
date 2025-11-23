# Gotchas

Non-obvious behaviors, edge cases, and things that are easy to get wrong.

**Format**: Each entry is a subsection with a title, timestamp (YYYY-MM-DD), and content (bullet points).
If updating existing content significantly, note it: `## Topic (2025-10-08, updated 2025-11-15)`

---

## Testing (2025-10-08, updated 2025-10-24)

- E2E tests (`pytest -m e2e`) are SLOW - use sparingly, prefer unit tests for rapid iteration
- Always run `./scripts/dev_checks.sh` before committing - formats, lints, type-checks, and tests
- **OTel disabled in tests**: Set `OTEL_ENABLED=false` in test environment to avoid connection errors to Tempo endpoint. Module-level `tracer = trace.get_tracer()` calls trigger OTel initialization at import time.
- **LiteLLM type warnings**: When working with `ModelResponse`, use proper typed objects (`Choices`, `StreamingChoices`, `Message`, `Delta`) to avoid Pydantic serialization warnings from LiteLLM's `Union` types. See test fixtures for examples.
- **E2E tests**: E2E tests remain (test_gateway_matrix.py, test_streaming_chunk_structure.py).

## Docker Development (2025-10-08, updated 2025-11-21)

- **Env changes require recreate, not restart**: `docker compose restart gateway` updates the code, but does NOT reload `.env` changes - it reuses the existing container config. Use `docker compose up -d gateway` to recreate with updated env vars.
- Check logs with `docker compose logs -f gateway` when debugging
- Long-running compose or `uv` commands can hang the CLI; launch them via `scripts/run_bg_command.sh` so you can poll logs (`tail -f`) and terminate with the recorded PID if needed.
- **Services**: gateway, local-llm, db, and redis

## Observability Checks (2025-10-08, updated 2025-11-11)

- Uses OpenTelemetry for observability - see `dev/observability-v2.md` and `dev/VIEWING_TRACES_GUIDE.md`
- Live activity monitoring available at `/activity/monitor` on the gateway

## Documentation Structure (2025-10-10, updated 2025-11-11)

- **Active docs**: dev/ARCHITECTURE.md, dev/event_driven_policy_guide.md, dev/observability-v2.md, dev/VIEWING_TRACES_GUIDE.md
- **Common places to check**: README.md, CLAUDE.md, AGENTS.md, dev planning docs, inline code comments
- **Streaming behavior**: Emits conversation events via `storage/events.py` using background queue for non-blocking persistence

## Queue Shutdown for Stream Termination (2025-01-20, updated 2025-10-20)

**Gotcha**: Use `asyncio.Queue.shutdown()` for stream termination, not `None` sentinel values

- **Why**: Python 3.11+ built-in queue shutdown raises `QueueShutDown` when drained - cleaner than sentinel patterns
- **Wrong**: Putting `None` (gets consumed/lost during batch draining)
- **Right**: Call `queue.shutdown()` to signal end; catch `QueueShutDown` exception
- **Batch processing**: Block with `await queue.get()` for first item (don't busy-wait with `get_nowait()` in loop!)
- **Why it matters**: Busy-wait consumes 100% CPU; proper blocking is essential for async efficiency

## Anthropic SSE Requires Stateful Block Index Tracking (2025-11-03)

- OpenAI chunks lack block indices; Anthropic clients need sequential indices (0,1,2...) + lifecycle events (start/delta/stop)
- Use `AnthropicSSEAssembler` to maintain state across chunks (llm/anthropic_sse_assembler.py)

## Policies Must Forward Content Chunks (2025-11-04)

- If policy handles tool calls but doesn't implement `on_content_delta()`, content never reaches client
- Always forward content chunks in `on_content_delta()` (see tool_call_judge_policy.py:146)
- Use `Delta(content=text)` not `{"content": text}` - dicts break SSE assembly
- Use `StreamingChoices` not `Choices` for streaming (utils.py create_text_chunk)

## finish_reason Must Be in Separate Final Chunk for Tool Calls (2025-11-21)

**The Bug**: When `SimplePolicy` or `ToolCallJudgePolicy` buffered multiple tool calls and emitted them via `create_tool_call_chunk()`, Claude Code would interpret the response as **multiple separate responses** instead of one response with multiple tool calls.

**Root Cause**: `create_tool_call_chunk()` was hardcoding `finish_reason="tool_calls"` in every tool call chunk. When there were 4 tool calls, Claude Code saw 4 chunks with `finish_reason="tool_calls"` and interpreted each as a complete response.

**The Correct Pattern** (from actual Anthropic API responses):
- All intermediate chunks have `finish_reason=null`
- Only the final chunk has `finish_reason="tool_calls"` with an **empty delta**

```
Chunk   1: finish_reason=None         content=True  tool_calls=False
Chunk   2: finish_reason=None         content=False tool_calls=True  | tool[0]
...
Chunk  24: finish_reason=None         content=False tool_calls=True  | tool[3]
Chunk  25: finish_reason=tool_calls   content=False tool_calls=False  <- Final chunk, empty delta
```

**The Fix**:
1. `create_tool_call_chunk()` now defaults `finish_reason=None`
2. Policies that buffer tool calls must emit the `finish_reason` in `on_stream_complete()`
3. `PolicyExecutor` drains the egress queue after `on_stream_complete()` to ensure the finish chunk is sent

**Impact**: Any policy that buffers tool calls and uses `create_tool_call_chunk()` must now implement `on_stream_complete()` to emit the final `finish_reason` chunk. See `SimplePolicy.on_stream_complete()` or `ToolCallJudgePolicy.on_stream_complete()` for the pattern.

**Related Files**:
- `src/luthien_proxy/policy_core/chunk_builders.py` - `create_tool_call_chunk()`
- `src/luthien_proxy/policies/simple_policy.py` - `on_stream_complete()`
- `src/luthien_proxy/policies/tool_call_judge_policy.py` - `on_stream_complete()`
- `src/luthien_proxy/streaming/policy_executor/executor.py` - egress queue drain after `on_stream_complete()`

---

(Add gotchas as discovered with timestamps: YYYY-MM-DD)
