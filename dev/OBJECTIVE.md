# Objective

Implement a V3 event-based tool call judge policy using the new EventBasedPolicy architecture.

## Goal

Create a simpler, more legible version of the tool call judge policy that:
- Uses the V3 EventBasedPolicy hooks (on_content_delta, on_tool_call_delta, on_tool_call_complete, etc.)
- Eliminates manual buffering complexity (no need for StreamChunkAggregator)
- Has straightforward logic with no complex state management
- Uses PolicyContext.scratchpad for per-request state tracking
- Uses StreamingContext.is_output_finished() to prevent sending after blocking
- Uses build_block_chunk() to convert complete tool calls to chunks

## Acceptance Criteria

- [ ] New policy class `ToolCallJudgeV3Policy` implemented in `src/luthien_proxy/v2/policies/tool_call_judge_v3.py`
- [ ] Policy extends `EventBasedPolicy` and overrides only needed hooks
- [ ] Content deltas use default forwarding (no override of on_content_delta)
- [ ] Tool call deltas are NOT forwarded (override on_tool_call_delta with pass)
- [ ] Tool calls are judged in on_tool_call_complete hook
- [ ] Passed tool calls are sent using build_block_chunk()
- [ ] Blocked tool calls send replacement text and mark output finished
- [ ] Policy continues processing stream after blocking for observability
- [ ] Metrics tracked in context.scratchpad (tool_calls_judged, tool_calls_blocked)
- [ ] Comprehensive tests validate all behavior
- [ ] All dev checks pass (format, lint, type check, tests)
