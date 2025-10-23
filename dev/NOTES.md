# Notes

_This file is used for scratchpad notes during active development. It is cleared when wrapping up objectives._

---

**For current implementation status**, see:
- [`dev/v2_architecture_design.md`](v2_architecture_design.md) - V2 architecture and implementation status
- [`dev/observability-v2.md`](observability-v2.md) - Observability implementation status

---

## 2025-10-22: Tool Call Judge - Simple State Machine Refactor

### Current Implementation (Refactored)

**Design principle**: Conceptually simple state machine that buffers everything, then processes the buffer head-first.

#### State Machine Algorithm

```
Buffer: [] - all incoming chunks go here first
Stream ended: false

Loop:
    1. Buffer incoming chunks (if stream not ended)

    2. While first chunk in buffer is NOT a tool call:
        Forward it
        Remove from buffer

    3. If first chunk in buffer IS a tool call:
        Find where tool call ends
        If tool call is complete:
            Create aggregator for just this tool call
            Judge the tool call
            If approved:
                Forward tool call chunks
                Remove from buffer
                Continue loop (goto step 2)
            If blocked:
                Send rejection message
                Terminate stream
        Else if stream ended with incomplete tool call:
            Judge incomplete tool call (fail-safe)
            If approved: forward buffer
            If blocked: send rejection
            Terminate
```

#### Key Properties

1. **Serial evaluation**: Tool calls are judged sequentially, one at a time
2. **No bypass**: All chunks go through buffer first
3. **Simple to reason about**: Clear queue-based processing of buffer head
4. **Fail-safe**: Incomplete tool calls are evaluated (can be blocked)
5. **Low latency for text**: Non-tool-call content forwarded immediately

#### Implementation Details

- Located in `src/luthien_proxy/v2/policies/tool_call_judge.py::process_streaming_response`
- Each tool call gets its own fresh `StreamChunkAggregator`
- Judge is called synchronously (blocks until response)
- First blocked tool call terminates the entire stream

### Tests Added

1. ✅ `test_buffer_state_machine_simple` - Text → Tool A (approve) → Text → Tool B (block)
2. ✅ `test_interleaved_tool_calls_and_text` - Tool → Text → Tool → Text → Tool (all approved)
3. ✅ All 23 tests passing
