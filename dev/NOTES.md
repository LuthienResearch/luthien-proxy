# Objective Notes

Park quick findings, design snippets, or open questions here while the current objective is active. Keep entries dated, and prune anything that graduates into a TODO or changelog entry.

---

## 2025-10-05: Streaming Judge Policy Debug

### Problem
Judge policy correctly blocks harmful tool calls in non-streaming mode but fails in streaming mode - the BLOCKED response never reaches the client.

### Root Cause
**Race condition in streaming pipeline** between proxy callback and control plane policy processing.

#### Timing Breakdown
1. Dummy provider sends 2 chunks quickly (role + tool_calls with finish_reason="tool_calls")
2. Proxy forwards both to control plane via WebSocket
3. Control plane buffers, detects finish_reason, starts judge evaluation (~170ms)
4. **Meanwhile**: Proxy finishes `async for item in response` (upstream done)
5. Proxy calls `poll_control(initial_timeout=0.05)` - waits only **50ms**
6. Proxy times out, enters `finally` block, calls `_cleanup_stream()`, closes WebSocket
7. Control plane finishes BLOCKED response, tries `websocket.send_json()` → WebSocketDisconnect
8. BLOCKED chunk never sent to proxy, never yielded to client

#### Evidence from Logs (stream f81b9466-066a-401c-ba6e-8fc94d6f341d)
```
00:28:03.122 - Judge evaluation starts
00:28:03.289 - Tool call BLOCKED (167ms elapsed)
00:28:03.290 - "stream disconnected during processing"
```

**Judge takes 167ms, but proxy only waits 50ms.**

### Solution
Increase the `poll_control` timeout after upstream finishes to give control plane time to complete buffered processing.

**File**: `config/litellm_callback.py`
**Line**: ~388 in `async_post_call_streaming_iterator_hook`

Change from:
```python
for transformed in await poll_control(initial_timeout=0.05):
```

To:
```python
for transformed in await poll_control(initial_timeout=0.5):  # 500ms for policy processing
```

This gives the control plane up to 500ms to finish processing buffered tool calls before the proxy closes the connection.

### Alternative Considered
Keep polling until control plane signals END, but this adds complexity. The simpler timeout increase should suffice since judge evaluation is deterministic and bounded.

### Test Plan
1. Apply fix to `config/litellm_callback.py`
2. Run: `uv run pytest tests/e2e_tests/test_policies_parameterized.py::test_policy_streaming -k judge_blocks`
3. Verify BLOCKED content appears in response
4. Run all policy streaming tests to ensure no regressions
