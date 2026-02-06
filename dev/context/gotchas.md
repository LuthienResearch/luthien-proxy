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

## Docker Development (2025-10-08, updated 2026-02-03)

- **Shell env overrides .env for API keys**: Docker Compose's `${VAR}` syntax checks shell environment FIRST, `.env` file second. If `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` is set in your shell (e.g., from `~/.zshrc`), it will override `.env` values. The fix is to use `env_file: .env` in docker-compose.yaml and NOT list API keys in the `environment:` block.
- **Env changes require recreate, not restart**: `docker compose restart gateway` updates the code, but does NOT reload `.env` changes - it reuses the existing container config. Use `docker compose up -d gateway` to recreate with updated env vars.
- Check logs with `docker compose logs -f gateway` when debugging
- Long-running compose or `uv` commands can hang the CLI; launch them via `scripts/run_bg_command.sh` so you can poll logs (`tail -f`) and terminate with the recorded PID if needed.
- **Services**: gateway, db, and redis

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

**Gotcha**: Multiple tool calls streamed with `finish_reason` on each chunk causes clients to interpret them as separate responses.

- **Wrong**: `create_tool_call_chunk(tool, finish_reason="tool_calls")` for each tool
- **Right**: `create_tool_call_chunk(tool)` for each tool, then `create_finish_chunk("tool_calls")` once at end in `on_stream_complete()`

See `SimplePolicy.on_stream_complete()` for the pattern.

## Claude Code --tools Flag Restricts Available Tools (2025-11-24)

**Gotcha**: Using `--tools "Bash(echo:*)"` or similar permission patterns in Claude Code's `-p` mode unexpectedly restricts the tool list to only MCP tools, removing built-in tools like `Bash`, `Read`, `Edit`.

- **Wrong**: `claude -p --tools "Bash(echo:*)" "run echo hello"` - Bash tool not available
- **Right**: `claude -p "run echo hello"` - uses default tool set including Bash
- **Why**: The `--tools` flag with permission patterns appears to filter the tool list differently than expected. When testing Claude Code through the gateway, omit `--tools` to get the full default tool set.

## Image/Multimodal Handling Through Proxy (2025-12-15)

**Gotcha**: Images pass validation after PR #104 fix, but Claude may respond to wrong image content.

- **Fixed (PR #104)**: Validation error - changed `Request.messages` to `list[dict[str, Any]]`, added image block conversion
- **Still broken**: Claude sometimes describes wrong image - suspect LiteLLM→Anthropic conversion issue
- **Tracking**: Issue #108 has full troubleshooting logs

## Thinking Blocks Must Come First in Anthropic Responses (2026-01-14)

- Anthropic API requires `thinking`/`redacted_thinking` blocks BEFORE text content
- LiteLLM exposes these via `message.thinking_blocks` (list of dicts)
- Wrong order causes: `Expected 'thinking' or 'redacted_thinking', but found 'text'`

## Thinking Blocks in Multi-Turn Conversations (2026-01-24)

**Gotcha**: Extended thinking requires fixes at THREE layers - streaming, format conversion, AND request validation.

1. **Streaming assembler** must recognize `reasoning_content` and `thinking_blocks` from LiteLLM
2. **Format conversion** (`anthropic_to_openai_request`) must preserve thinking blocks in message history - they were silently dropped!
3. **Pydantic validation** must allow list content in `AssistantMessage` - OpenAI types don't natively support thinking blocks

**Symptoms by layer**:
- Layer 1 missing: Single-turn works, but no thinking content visible
- Layer 2 missing: 500 error from Anthropic: `"Expected 'thinking' or 'redacted_thinking', but found 'text'"`
- Layer 3 missing: 400 error from proxy: `"AssistantMessage.content: Input should be a valid string"`

**Files involved**: `anthropic_sse_assembler.py`, `llm_format_utils.py`, `types/anthropic.py`, `types/openai.py`

## LiteLLM Delivers Thinking Signatures Out of Order (2026-01-24)

**Gotcha**: LiteLLM sends `signature_delta` AFTER text content starts, but Anthropic requires it BEFORE the thinking block closes.

**Expected order** (Anthropic native):
1. thinking_deltas → 2. signature_delta → 3. content_block_stop → 4. text starts

**LiteLLM actual order**:
1. thinking_deltas → 2. text starts → 3. signature_delta (too late!)

**Fix**: Delay `content_block_stop` for thinking blocks until signature arrives. Track `thinking_block_needs_close` flag and `last_thinking_block_index`.

## LiteLLM >= 1.81.0 Breaking Changes in Streaming (2026-01-29)

**Gotcha**: litellm 1.81.0+ introduced breaking changes in streaming response handling.

**Breaking changes**:
1. `StreamingChoices.delta` returns a `dict` instead of a `Delta` object
2. `finish_reason` defaults to `"stop"` instead of `None` for intermediate chunks
3. `StreamingChoices` passed to `ModelResponse` may get converted to `Choices`

**Fix**: Use `response_normalizer.py` to normalize all streaming chunks:
- `normalize_chunk()` - converts dict deltas to `Delta` objects
- `normalize_chunk_with_finish_reason()` - also restores intended `finish_reason`
- `normalize_stream()` - wraps async streams to normalize each chunk

**Where normalization happens**:
- `litellm_client.py` calls `normalize_stream()` on raw litellm output
- Downstream code should receive already-normalized chunks

**Test helpers**: Use `litellm_test_utils.make_streaming_chunk()` to create normalized chunks for tests.

## Content and finish_reason Must Be in Separate Chunks (2026-01-30)

**Gotcha**: The SSE assembler's `convert_chunk_to_event()` returns early when there's text content, never reaching the `finish_reason` check. Creating a chunk with BOTH content AND finish_reason causes the finish_reason to be silently ignored.

**Symptoms**:
- Claude Code shows blank responses with "No assistant message found" errors
- SSE stream missing `content_block_stop` and `message_delta` events
- `message_stop` appears but without proper block closure

**Wrong**: Creating a single chunk with content + finish_reason
```python
chunk = create_text_chunk(text=transformed, finish_reason=stream_state.finish_reason)
ctx.push_chunk(chunk)  # finish_reason is ignored!
```

**Right**: Emit content first, then finish_reason in a separate chunk
```python
content_chunk = create_text_chunk(text=transformed, finish_reason=None)
ctx.push_chunk(content_chunk)

if stream_state.finish_reason:
    finish_chunk = create_finish_chunk(finish_reason=stream_state.finish_reason)
    ctx.push_chunk(finish_chunk)
```

**Why**: `convert_chunk_to_event()` checks content before finish_reason. If content exists, it returns a `text_delta` event immediately. The finish_reason check never runs.

**Affected policies**: Any policy that buffers content and emits it in `on_content_complete()`. See `StringReplacementPolicy` for the correct pattern.

---

(Add gotchas as discovered with timestamps: YYYY-MM-DD)
