# Streaming Plan

## Objectives
- Maintain per-call, append-only buffers for original and final token streams.
- Emit granular chunk events (`original_chunk`, `final_chunk`) keyed by monotonically increasing `chunk_index`.
- Render canonical call snapshots on the backend so the UI can hydrate without replaying events.
- Preserve responsive streaming by letting the UI apply chunk events optimistically; fall back to snapshots only to recover from missed events.
- Avoid buffer reuse; create new buffers per call and close them when the call completes.

## Data Contract
### ConversationEvent
```
{
  call_id: string,
  trace_id: string,
  event_type: 'request_started' | 'original_chunk' | 'final_chunk' | 'request_completed',
  sequence: int,
  timestamp: datetime,
  payload: {
    chunk_index?: int,
    delta?: string,
    status?: 'success' | 'stream_summary' | 'failure',
    original_response?: string,
    final_response?: string,
    original_messages?: [{ role, content }],
    final_messages?: [{ role, content }]
  }
}
```
- `chunk_index` increases by 1 for each stream-specific chunk.
- `request_started` resets the per-call buffers.
- `request_completed` marks buffers closed for the call.

### ConversationCallSnapshot
```
{
  call_id: string,
  trace_id: string,
  started_at: datetime,
  completed_at: datetime | null,
  status: 'pending' | 'streaming' | 'success' | 'stream_summary' | 'failure',
  new_messages: [{ role, original, final }],
  original_response: string,
  final_response: string,
  chunk_count: int
}
```
- Derived from reassembling per-call buffers.
- Exposed via `/api/hooks/conversation` and `/api/hooks/conversation/by_trace` for UI hydration.

## Implementation Steps
1. **Backend normalizer**
   - Track per-call buffers in `_build_call_snapshots` using chunk indices.
   - Ensure `chunk_index` in `_build_conversation_events` increments with each chunk.
   - Extend snapshots to include call-level metadata and response text assembled from buffers.

2. **Frontend updates**
   - Render turns directly from `snapshot.calls`, removing client event replay state.
   - Listen to streaming chunk events only to append live text until `request_completed`.
   - After `request_completed`, close buffers on the client and rely on snapshots for final reconciliation.

3. **Testing**
   - Update unit tests to assert call snapshots contain expected `new_messages`, chunk counts, and responses.
   - Add fixture tests for multi-call traces to ensure per-call buffer isolation.
