# Conversation Storage Schema Migration Plan

## Goal

Migrate from the current three-table streaming-chunk-based schema to a simplified two-table request/response schema with a separate policy_events table.

## Current Schema (to be replaced)

- `conversation_calls` - Parent record with trace_id, model, status, metadata
- `conversation_events` - Streaming chunks with chunk_index, delta_text, raw_chunk, choice_index, role
- `conversation_tool_calls` - Denormalized tool call extraction
- `conversation_judge_decisions` - Judge-specific decision records

## Target Schema

- `conversation_calls` - Lightweight parent: call_id, model_name, provider, status, created_at, completed_at
- `conversation_events` - Request/response pairs: id, call_id, event_type ('request'|'response'), sequence, payload (jsonb), created_at
- `policy_events` - Generic policy actions: id, call_id, policy_class, policy_config, event_type, original_event_id, modified_event_id, metadata, created_at

**Removed**: trace_id everywhere, conversation_tool_calls, conversation_judge_decisions, all chunk-level columns

## Migration Steps

### Phase 1: Schema Definition

1. **Create new Prisma schema models**
   - Define new `conversation_calls` (simplified)
   - Define new `conversation_events` with event_type enum
   - Define new `policy_events` table
   - Keep old tables temporarily for dual-write phase

2. **Generate migration SQL**
   - Use Prisma migrate to create new tables alongside old ones
   - Add temporary suffix to new tables (`_v2`) to avoid conflicts

### Phase 2: Dual-Write Implementation

3. **Update conversation event builders** (`conversation/events.py`)
   - Keep existing `build_conversation_events()` for backward compatibility
   - Add new `build_conversation_events_v2()` that creates request/response events with OpenAI-format payloads
   - Extract full request payload (messages, model, params) for request events
   - Extract response message + metadata for response events
   - No chunk-level data in new events

4. **Update conversation store** (`conversation/store.py`)
   - Keep existing `record_conversation_events()` for old schema
   - Add new `record_conversation_events_v2()` for new schema
   - Dual-write: both functions called during transition period

5. **Update policy event recording**
   - Add `record_policy_event()` helper in new `conversation/policy_events.py`
   - Update judge policy to write to both old judge_decisions and new policy_events
   - Policy metadata maps: probability, explanation, timing, etc. → metadata jsonb

6. **Update all hook call sites**
   - `hooks_routes.py` - dual-write on all hooks
   - `streaming_routes.py` - dual-write streaming events
   - Keep both old and new event builders running in parallel

### Phase 3: Read Path Migration

7. **Add new read functions** (`conversation/db.py`)
   - `load_conversation_v2(call_id)` - returns request/response pairs from new schema
   - `load_policy_events(call_id)` - returns policy decisions
   - Keep old read functions active

8. **Create new API endpoints** (or feature-flag existing ones)
   - `/api/conversation/{call_id}/v2` - serves new format
   - `/api/policy-events/{call_id}` - serves policy decisions
   - Old endpoints continue using old schema

9. **Update UI/frontend to consume new endpoints**
   - Conversation monitor UI switches to v2 endpoints
   - Judge decision UI switches to policy_events endpoint

### Phase 4: Validation & Cutover

10. **Add data validation**
    - Background job compares old vs new schema for same call_ids
    - Alert on discrepancies (missing events, payload mismatches)
    - Run for 1-2 weeks to build confidence

11. **Feature flag cutover**
    - Add `USE_CONVERSATION_SCHEMA_V2` env var (default: false)
    - When true, skip old schema writes
    - Monitor for errors, roll back if needed

12. **Stop writing to old schema**
    - Set `USE_CONVERSATION_SCHEMA_V2=true` in production
    - Remove dual-write code paths after burn-in period

### Phase 5: Cleanup

13. **Remove old schema dependencies**
    - Delete old read functions from `conversation/db.py`
    - Delete old event builders from `conversation/events.py`
    - Delete old store functions from `conversation/store.py`
    - Remove trace_id references from all policies
    - Update all tests to use new schema

14. **Drop old tables**
    - Create migration to drop conversation_events (old), conversation_tool_calls, conversation_judge_decisions
    - Rename conversation_events_v2 → conversation_events
    - Remove temporary `_v2` suffixes

15. **Update documentation**
    - API documentation reflects new schema
    - Remove obsolete sections from codebase_learnings.md
    - Update decisions.md to mark migration complete

## Row-Based Retention (Future Work)

After migration stabilizes:

- Add `CONVERSATION_EVENTS_MAX_ROWS` config (default: 100,000)
- Implement periodic cleanup job to delete oldest call_ids when limit exceeded
- Apply same retention to debug_logs table

## Testing Strategy

- **Unit tests**: Mock old and new storage, verify dual-write correctness
- **Integration tests**: Write events via hooks, verify both schemas populated correctly
- **E2E tests**: Full request/response cycle, verify new schema can reconstruct conversation
- **Data validation**: Compare old vs new schema outputs for real traffic

## Rollback Plan

At any phase, rollback is possible:

- Phase 1-2: No user impact, just delete new tables
- Phase 3-4: Keep old endpoints active, revert UI to old endpoints
- Phase 5+: Must restore from backup if old tables dropped

## Open Questions

1. **What to do with existing data in old tables?**
   - Option A: Backfill new schema from old (complex, may be lossy)
   - Option B: Start fresh, archive old data (simpler)
   - **Recommendation**: Start fresh, keep old tables read-only for historical lookups

2. **How to handle in-flight requests during cutover?**
   - Dual-write ensures both schemas populated
   - Read from old or new based on feature flag
   - **Recommendation**: Cutover during low-traffic window

3. **Streaming chunk data in debug_logs - keep or prune?**
   - Still useful for debugging chunk-level issues
   - **Recommendation**: Keep debug_logs unchanged, apply same retention policy

## Timeline Estimate

- Phase 1: 1 day (schema definition)
- Phase 2: 3-4 days (dual-write implementation)
- Phase 3: 2-3 days (read path migration)
- Phase 4: 1-2 weeks (validation + cutover)
- Phase 5: 2-3 days (cleanup)

**Total**: ~3-4 weeks with validation period

## Success Criteria

- [ ] New schema stores complete request/response turns in OpenAI format
- [ ] No chunk-level data in conversation_events
- [ ] Policy events table captures judge decisions and other policy actions
- [ ] All UIs work with new schema
- [ ] Tests pass with new schema
- [ ] Old tables dropped from production
- [ ] Performance equivalent or better than old schema
