# Dataflow Legibility Improvements

## Codex Review Summary (2025-10-10)

### First Review
**Feedback incorporated**:
1. ✅ **Priority reordering** - Reading guide now Priority 1 (immediate value, zero risk)
2. ✅ **Helper made synchronous** - `log_and_publish_hook_result` no longer async (simpler, less error-prone)
3. ✅ **Timestamp consistency** - Helper captures timestamps internally (no drift between logs)
4. ✅ **Added policy parameter helper** - `prepare_policy_payload` isolates signature filtering logic

### Second Review
**Final issues fixed**:
1. ✅ **Missing imports** - Added `time`, `timezone`, `Callable`, and `inspect` to helper module
2. ✅ **Section numbering** - Fixed inconsistent priority numbering throughout document

### Third Review (Joint Claude Code + Codex Discussion)
**Agreements reached**:
1. ✅ **Docstrings before reading guide** - Move DATAFLOW docstrings to Priority 2 so reading guide references are immediately valid
2. ✅ **Keep single helper function** - No sub-helpers for `log_and_publish_hook_result`; ensure clear variable names and linear flow
3. ✅ **Document streaming path better** - Add _StreamEventPublisher documentation and reading guide section for streaming
4. ✅ **Section headers over breadcrumbs** - Use visual markers (`# === SECTION ===`) instead of line-by-line "next steps" comments

**Additional improvements**:
- Add payload shape references to reading guide (helps readers understand data structures flowing through the system)

**Result**: Plan refined and ready for implementation. Both agents satisfied with legibility focus.

---

## Problem Statement

The basic dataflow logic in Luthien is sound, but tracing it through the codebase requires jumping between 5-8 files. While individual files are clear about **what** they do, it's hard to follow **where data goes next**.

### Example: Non-Streaming Hook Flow

To trace a single hook result from callback to database and Redis, you must read:

1. `config/unified_callback.py` - callback invocation
2. `control_plane/hooks_routes.py:80-173` - generic handler
3. `control_plane/conversation/events.py` - build conversation events
4. `control_plane/conversation/store.py` - write to database
5. `control_plane/conversation/streams.py` - publish to Redis
6. `control_plane/utils/task_queue.py` - understand background task submission

That's 6 files to understand one hook's complete flow.

## Root Cause

The "log → persist → publish" logic is scattered across ~40 lines in `hooks_routes.py` with direct calls to multiple subsystems:

```python
# Line 114: Log original
DEBUG_LOG_QUEUE.submit(debug_writer(f"hook:{hook_name}", stored_record))

# Lines 117-133: Invoke policy
handler = getattr(policy, hook_name.lower(), None)
if handler:
    # ... parameter filtering logic ...
    handler_result = await handler(**filtered_payload)

# Line 147: Log result
DEBUG_LOG_QUEUE.submit(debug_writer(f"hook_result:{hook_name}", result_record))

# Lines 152-160: Build conversation events
events = build_conversation_events(
    hook=hook_name,
    call_id=call_id,
    # ... many params ...
)

# Line 162: Submit to DB queue
if events:
    CONVERSATION_EVENT_QUEUE.submit(record_conversation_events(pool, events))

# Lines 163-164: Publish to Redis
for event in events:
    CONVERSATION_EVENT_QUEUE.submit(publish_conversation_event(redis_conn, event))
```

This obscures the main logic: "invoke policy, then log/persist/publish result."

## Proposed Solutions

### Priority 1: Create Reading Guide Draft (15 min)

**Impact**: High - immediate onboarding value
**Effort**: Minimal - just documentation

**Note**: Reading guide will reference DATAFLOW docstrings added in Priority 2. The guide can be created first with a note that docstrings are forthcoming, or created after Priority 2 for immediate accuracy.

(See Reading Guide Implementation section below for full details)

### Priority 2: Add DATAFLOW Docstrings (10 min)

**Impact**: High - makes code self-documenting and reading guide references valid
**Effort**: Minimal - just docstring additions

**Add to `hooks_routes.py:hook_generic()`:**

```python
async def hook_generic(...) -> JSONValue:
    """Generic hook endpoint for any CustomLogger hook.

    DATAFLOW:
    1. Log original payload → DEBUG_LOG_QUEUE → debug_logs table
    2. Invoke policy.{hook_name}(**payload) → get transformed result
    3. Log/persist/publish result:
       - DEBUG_LOG_QUEUE → debug_logs table
       - CONVERSATION_EVENT_QUEUE → conversation_events table
       - CONVERSATION_EVENT_QUEUE → Redis pub/sub (luthien:conversation:{call_id})
    4. Return result to callback

    See: hook_result_handler.py for logging/publishing implementation
    """
```

**Add to `streaming_routes.py:policy_stream_endpoint()`:**

```python
async def policy_stream_endpoint(websocket: WebSocket, stream_id: str) -> None:
    """Coordinate streaming requests between proxy and policies.

    DATAFLOW (per chunk):
    1. Receive CHUNK from callback via WebSocket
    2. Log original → debug_logs
    3. Yield to policy.generate_response_stream()
    4. Policy yields transformed chunk(s) [0..N]
    5. Log transformed → debug_logs
    6. Publish → Redis pub/sub
    7. Send CHUNK back to callback via WebSocket

    See: _StreamEventPublisher for logging/publishing implementation
    """
```

**Add section headers in code** for visual chunking:
- `# === PAYLOAD PREPARATION ===`
- `# === POLICY INVOCATION ===`
- `# === RESULT LOGGING/PUBLISHING ===`

**Benefits**:
- Code is self-documenting without needing external docs
- Reading guide references become immediately useful
- Visual structure helps navigate long functions

### Priority 3: Extract Logging/Publishing Helper (45 min)

**Impact**: High - makes main flow immediately clear
**Effort**: Low - straightforward refactor

**Codex feedback addressed**:
- Make helper synchronous (doesn't need to be async)
- Reduce parameter count by capturing timestamps internally
- Add policy parameter filtering helper

Create `src/luthien_proxy/control_plane/hook_result_handler.py`:

```python
"""Helper for logging, persisting, and publishing hook results."""

import inspect
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from luthien_proxy.control_plane.conversation import (
    build_conversation_events,
    publish_conversation_event,
    record_conversation_events,
)
from luthien_proxy.control_plane.dependencies import DebugLogWriter
from luthien_proxy.control_plane.utils.task_queue import (
    CONVERSATION_EVENT_QUEUE,
    DEBUG_LOG_QUEUE,
)
from luthien_proxy.types import JSONObject
from luthien_proxy.utils import db, redis_client


def log_and_publish_hook_result(
    *,
    hook_name: str,
    call_id: Optional[str],
    trace_id: Optional[str],
    original_payload: JSONObject,
    result_payload: JSONObject,
    debug_writer: DebugLogWriter,
    redis_conn: redis_client.RedisClient,
    db_pool: Optional[db.DatabasePool],
) -> None:
    """Log hook result to debug_logs, record conversation events, publish to Redis.

    This encapsulates the standard post-policy workflow:
    1. Write result to debug_logs via debug_writer
    2. Build and record conversation_events (if call_id available)
    3. Publish events to Redis pub/sub

    All operations run in background via task queues and are best-effort.

    Note: Synchronous function that submits async work to queues.
    Timestamps captured internally for consistency.
    """
    # Capture timestamps once for consistency
    timestamp_ns = time.time_ns()
    timestamp = datetime.now(timezone.utc)

    # Log result to debug_logs
    result_record: JSONObject = {
        "hook": hook_name,
        "litellm_call_id": call_id,
        "original": original_payload,
        "result": result_payload,
        "post_time_ns": timestamp_ns,
    }
    if trace_id:
        result_record["litellm_trace_id"] = trace_id

    DEBUG_LOG_QUEUE.submit(debug_writer(f"hook_result:{hook_name}", result_record))

    # Build and persist conversation events (if we have a call_id)
    if isinstance(call_id, str) and call_id:
        events = build_conversation_events(
            hook=hook_name,
            call_id=call_id,
            trace_id=trace_id,
            original=original_payload,
            result=result_payload,
            timestamp_ns_fallback=timestamp_ns,
            timestamp=timestamp,
        )

        if events:
            # Submit to database
            CONVERSATION_EVENT_QUEUE.submit(record_conversation_events(db_pool, events))

            # Publish to Redis
            for event in events:
                CONVERSATION_EVENT_QUEUE.submit(publish_conversation_event(redis_conn, event))


def prepare_policy_payload(handler: Callable, payload: JSONObject) -> JSONObject:
    """Filter payload to match policy handler's signature.

    Policies can accept **kwargs (get full payload) or specific named parameters.
    This inspects the handler signature and returns only matching keys.
    """
    signature = inspect.signature(handler)
    parameters = signature.parameters

    # If handler accepts **kwargs, pass everything
    accepts_var_kw = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values())
    if accepts_var_kw:
        return payload

    # Otherwise, filter to named parameters
    parameter_names = {name for name in parameters.keys() if name != "self"}
    return {k: v for k, v in payload.items() if k in parameter_names}
```

Then refactor `hooks_routes.py:hook_generic()`:

```python
from luthien_proxy.control_plane.hook_result_handler import (
    log_and_publish_hook_result,
    prepare_policy_payload,
)

async def hook_generic(
    hook_name: str,
    payload: JSONObject,
    debug_writer: DebugLogWriter = Depends(get_debug_log_writer),
    policy: LuthienPolicy = Depends(get_active_policy),
    counters: Counter[str] = Depends(get_hook_counter_state),
    redis_conn: redis_client.RedisClient = Depends(get_redis_client),
    pool: db.DatabasePool | None = Depends(get_database_pool),
) -> JSONValue:
    """Generic hook endpoint for any CustomLogger hook.

    DATAFLOW:
    1. Log original payload → debug_logs (background)
    2. Invoke policy.{hook_name}(**payload) → get transformed result
    3. Log/persist/publish result via log_and_publish_hook_result()
       - debug_logs (background)
       - conversation_events (background)
       - Redis pub/sub (background)
    4. Return result to callback
    """
    try:
        # Prepare payload for logging and policy invocation
        record_payload = cast(JSONObject, json_safe(payload))
        stored_payload: JSONObject = deepcopy(record_payload)

        # Extract call_id and trace_id
        call_id = extract_call_id_for_hook(hook_name, payload)
        trace_id = extract_trace_id(payload)

        # Log original payload to debug_logs (background task)
        # Capture timestamp once for consistency across original and result logs
        timestamp_ns = time.time_ns()
        record: JSONObject = {"hook": hook_name, "payload": record_payload, "post_time_ns": timestamp_ns}
        if isinstance(call_id, str) and call_id:
            record["litellm_call_id"] = call_id
        if trace_id:
            record["litellm_trace_id"] = trace_id
        DEBUG_LOG_QUEUE.submit(debug_writer(f"hook:{hook_name}", record))

        # Increment hook counter
        counters[hook_name.lower()] += 1

        # Invoke policy handler (if exists)
        handler = cast(
            Optional[Callable[..., Awaitable[JSONValue | None]]],
            getattr(policy, hook_name.lower(), None),
        )

        handler_result: JSONValue | None = None
        if handler:
            policy_payload = cast(JSONObject, strip_post_time_ns(payload))
            filtered_payload = prepare_policy_payload(handler, policy_payload)
            handler_result = await handler(**filtered_payload)

        final_result: JSONValue = handler_result if handler_result is not None else payload
        sanitized_result = json_safe(final_result)

        # Log, persist, and publish result (background tasks)
        log_and_publish_hook_result(
            hook_name=hook_name,
            call_id=call_id if isinstance(call_id, str) else None,
            trace_id=trace_id,
            original_payload=stored_payload,
            result_payload=sanitized_result,
            debug_writer=debug_writer,
            redis_conn=redis_conn,
            db_pool=pool,
        )

        # Return result to callback
        result_to_return = strip_post_time_ns(final_result)
        logger.info(f"Hook {hook_name} returning: type={type(result_to_return)}, preview={str(result_to_return)[:200]}")
        return result_to_return

    except Exception as exc:
        import traceback
        logger.error(f"hook_generic_error in {hook_name}: {exc}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"hook_generic_error in {hook_name}: {exc}")
```

**Benefits**:
- Main logic now ~60 lines instead of 90
- "What happens to results" is one clearly-named function call
- Helper can be reused in streaming routes
- Testing is easier (mock one function instead of 3 queues)

### Priority 4: Document Streaming Path + Payload Shapes (15 min)

**Impact**: Medium - completes the legibility story for both paths
**Effort**: Minimal - documentation additions

1. **Add docstring to `_StreamEventPublisher` class** explaining its role in streaming result handling (parallel to `log_and_publish_hook_result` for non-streaming)

2. **Update reading guide** to include a "Streaming Result Handling" section:
   - Explain how `_StreamEventPublisher` provides the same logging/publishing functionality for streaming
   - Show the parallel between non-streaming (helper function) and streaming (publisher class)
   - Link to the streaming DATAFLOW docstring added in Priority 2

3. **Add "Data Structures" section to reading guide**:
   - Link to key dataclasses/types that flow through the system
   - Examples: `ConversationEvent`, hook payload structures, chunk formats
   - Brief explanation of each with file references
   - Helps readers understand the shape of data at each step

**Benefits**:
- Readers understand both paths use the same conceptual model
- No confusion about "why is streaming different?"
- Clear reference for payload structures at each dataflow step
- Complete coverage of all dataflow patterns

### Reading Guide Implementation (Priority 1)

**Impact**: High - major onboarding win
**Effort**: Low - just documentation

Create `docs/reading-guide.md`:

```markdown
# Reading Guide: Understanding Luthien Dataflows

## New to Luthien?

Start with **visual understanding**, then dive into code:

1. **Architecture overview**: [docs/dataflows.md](dataflows.md)
   - Read the sequence diagram first (bottom of file)
   - Then read "Hook Flows" section

2. **Non-streaming flow**: Follow one request end-to-end
   - **Entry point**: `config/unified_callback.py:210` (`async_post_call_success_hook`)
     - Shows how callback POSTs to control plane
   - **Main handler**: `control_plane/hooks_routes.py:80` (`hook_generic`)
     - Read the docstring DATAFLOW section
     - Skim the function - don't trace into helpers yet
   - **Result handler**: `control_plane/hook_result_handler.py` (`log_and_publish_hook_result`)
     - Shows logging → database → Redis flow
   - **Stop here** on first read - you understand the full flow

3. **Streaming flow**: Follow chunks through the pipeline
   - **Entry point**: `config/unified_callback.py:289` (`async_post_call_streaming_iterator_hook`)
   - **Orchestrator**: `proxy/stream_orchestrator.py` (`StreamOrchestrator.run()`)
     - Focus on the `async for` loop - shows bidirectional flow
   - **Control plane**: `control_plane/streaming_routes.py:370` (`policy_stream_endpoint`)
     - Read docstring DATAFLOW section
     - See `_forward_policy_output()` for chunk forwarding logic

## Deep Dives

Once you understand the basic flow:

### Provider Normalization
- **File**: `proxy/stream_normalization.py` (`AnthropicToOpenAIAdapter`)
- **Why**: Converts Anthropic SSE events to OpenAI chunk format
- **Impact**: Policies never see provider-specific formats

### Database Schema
- **File**: `prisma/control_plane/schema.prisma`
- **Events**: `conversation/events.py` (`build_conversation_events`)
- **Storage**: `conversation/store.py` (`record_conversation_events`)

### Policy API
- **Base class**: `policies/base.py` (`LuthienPolicy`)
- **Example**: `policies/tool_call_buffer.py` (`ToolCallBufferPolicy`)
  - Shows streaming buffering pattern
  - Good reference for custom policies

## Common Questions

**Q: Where does the policy result go?**
A: `hooks_routes.py` → `log_and_publish_hook_result()` → 3 destinations:
   1. `debug_logs` table (via `DEBUG_LOG_QUEUE`)
   2. `conversation_events` table (via `CONVERSATION_EVENT_QUEUE`)
   3. Redis pub/sub channel `luthien:conversation:{call_id}`

**Q: How are streaming chunks forwarded?**
A: Bidirectional WebSocket between callback and control plane:
   - Callback → control plane: `{"type": "CHUNK", "data": <upstream>}`
   - Control plane → callback: `{"type": "CHUNK", "data": <policy_transformed>}`
   - See `stream_orchestrator.py:run()` for orchestration logic

**Q: What gets logged?**
A: Every line with `DEBUG_LOG_QUEUE` or `CONVERSATION_EVENT_QUEUE`
   - Original payloads: `f"hook:{hook_name}"`
   - Results: `f"hook_result:{hook_name}"`
   - Search codebase for `DEBUG_LOG_QUEUE.submit` to find all log points

**Q: How do I test my changes?**
A: Three levels:
   1. Unit tests: `uv run pytest tests/unit_tests`
   2. Integration: `uv run pytest tests/integration_tests`
   3. E2E (slow): `uv run pytest -m e2e`

**Q: How do I trace a live request?**
A: See [observability.md](observability.md) for docker log commands
   - Quick: `docker compose logs --no-color | grep "{call_id}"`

## Data Structures

Understanding the shape of data at each step:

**Hook Payloads**:
- Non-streaming hooks receive the full LiteLLM callback payload
- See `LiteLLM` callback documentation for exact schemas
- Key fields: `litellm_call_id`, `litellm_trace_id`, `messages`, `model`, etc.

**Conversation Events** ([conversation/events.py](../src/luthien_proxy/control_plane/conversation/events.py)):
- `ConversationEvent` - stored in database and published to Redis
- Built from hook payloads by `build_conversation_events()`
- Fields: `call_id`, `trace_id`, `event_type`, `timestamp`, `payload`, etc.

**Streaming Chunks**:
- OpenAI format: `{"choices": [{"delta": {...}, "index": 0}], ...}`
- Anthropic chunks normalized to OpenAI format via `AnthropicToOpenAIAdapter`
- See [proxy/stream_normalization.py](../src/luthien_proxy/proxy/stream_normalization.py)

**WebSocket Messages** ([proxy/stream_orchestrator.py](../src/luthien_proxy/proxy/stream_orchestrator.py)):
- `{"type": "CHUNK", "data": <chunk>}` - streaming chunk
- `{"type": "DONE"}` - end of stream
- `{"type": "ERROR", "error": <message>}` - error occurred
```

### Optional: Architecture Diagram in docs/

**Impact**: Medium - visual reference
**Effort**: Medium - requires drawing tool

Create ASCII art or mermaid diagram showing component boundaries and data flow.

## Implementation Order

**Final priority order (after joint Claude Code + Codex review)**:

1. **Create reading guide draft** (15 min) - immediate onboarding value, can reference forthcoming docstrings
2. **Add DATAFLOW docstrings + section headers** (10 min) - makes code self-documenting and guide references valid
3. **Extract helpers + refactor hooks_routes** (45 min) - biggest code clarity win
   - `log_and_publish_hook_result()` - single function for all result handling
   - `prepare_policy_payload()` - isolates parameter filtering logic
   - Add section headers in code: `# === POLICY INVOCATION ===`, etc.
4. **Document streaming path + payload shapes** (15 min) - completes the story for both dataflow patterns and adds data structure references
5. Stop here unless requested

**Total estimated time**: ~85 minutes

**Rationale**:
- Reading guide first provides immediate onboarding value with zero risk
- Docstrings make code self-documenting before refactoring
- Helper extraction happens after documentation so the refactored code matches what docs describe
- Streaming documentation and payload shape references last, after the pattern is established for non-streaming

## Testing Plan

After implementing #1-3:

1. **Verify refactor correctness**:
   - Run full test suite: `./scripts/dev_checks.sh`
   - Run E2E tests: `uv run pytest -m e2e`
   - All tests should pass with no behavior changes

2. **Verify legibility improvement**:
   - Ask someone unfamiliar with codebase to trace non-streaming flow
   - Time how long it takes to understand "what happens after policy execution"
   - Goal: <5 minutes using reading guide

## Success Metrics

**Before**:
- Tracing one hook result: 6 files, ~200 lines of code
- Understanding streaming: 8+ files
- No onboarding guide

**After**:
- Tracing one hook result: 2 files (`hooks_routes.py` + `hook_result_handler.py`), ~80 lines
- Clear docstrings point to next steps
- Reading guide provides structured learning path
