# Streaming Documentation Audit & Consolidation Plan

**Date**: 2025-10-06
**Purpose**: Review and consolidate streaming-related documentation in `dev/`

## Current Inventory

### Active/Current Documentation

1. **`streaming-architecture.md`** (17KB, created Oct 6)
   - **Status**: ✅ CURRENT - Just created
   - **Purpose**: Documents the current implemented architecture with sequence diagrams
   - **Content**:
     - Simplified + detailed sequence diagrams
     - Component documentation with file references
     - Message protocol specification
     - Error handling strategies
     - Design decisions
   - **Action**: **KEEP** - This is the canonical architecture reference

2. **`streaming-rewrite-plan.md`** (33KB, created Oct 5, updated Oct 5)
   - **Status**: ✅ IMPLEMENTED - Rewrite is complete
   - **Purpose**: Detailed implementation plan for the StreamOrchestrator rewrite
   - **Content**:
     - Problem analysis (old queue-based approach)
     - Protocol design
     - Implementation structure
     - Critical fixes (JSON serialization, keepalive, resource cleanup)
     - Migration steps
     - Success criteria (all ✅ complete)
   - **Action**: **ARCHIVE** - Move to historical record
   - **Rationale**: Implementation is done. Useful for historical context and design rationale, but not day-to-day reference

### Outdated/Historical Documentation

3. **`streaming_policy_architecture_plan.md`** (36KB, created Sep 30)
   - **Status**: ❌ SUPERSEDED - Describes different architecture
   - **Last updated**: Sep 30 (before rewrite)
   - **Purpose**: Original plan for stateful streaming via WebSocket
   - **Content**:
     - Problem: policies can't maintain state across chunks
     - Proposed: WebSocket-based streaming policy API
     - Detailed implementation plan
   - **Action**: **DELETE** - Superseded by current implementation
   - **Rationale**: This was the initial exploration. The actual implementation evolved significantly (StreamOrchestrator pattern, simpler architecture)

4. **`streaming_pipeline_instrumentation_plan.md`** (6.8KB, created Oct 4)
   - **Status**: ⚠️ PARTIALLY COMPLETE - Debug tooling plan
   - **Last updated**: Oct 4
   - **Purpose**: Build debugging capabilities for streaming pipeline
   - **Content**:
     - Pipeline steps (LiteLLM callback → WebSocket → Control plane → Policy)
     - Instrumentation at each step
     - WebSocket logging (✅ complete)
     - Callback chunk logging (✅ complete)
     - Other pipeline steps (marked ✅ complete)
   - **Action**: **DELETE or MERGE**
   - **Rationale**:
     - The instrumentation is built (WebSocket logging, chunk logging)
     - If keeping, merge relevant sections into `streaming-architecture.md` under "Debugging"
     - As standalone plan doc, it's outdated

5. **`conversation_trace/02_streaming_plan.md`** (2.5KB, created Sep 27)
   - **Status**: ❌ DIFFERENT CONCERN - About conversation trace streaming, not proxy streaming
   - **Purpose**: Plan for streaming conversation events (chunk indices, SSE)
   - **Content**:
     - ConversationEvent schema
     - Append-only buffers for original/final streams
     - Chunk indexing strategy
   - **Action**: **KEEP** - Different concern (UI streaming, not LiteLLM ↔ Control Plane streaming)
   - **Rationale**: This is about streaming conversation traces to the UI via SSE, not about the bidirectional LiteLLM streaming we just rewrote

6. **`conversation_trace/04_streaming_postmortem.md`** (5.9KB, created Sep 27)
   - **Status**: ✅ HISTORICAL RECORD
   - **Purpose**: Post-mortem of conversation trace system implementation
   - **Content**:
     - Information flow (client → control plane → DB → UI)
     - Event normalization
     - Chunk indexing
     - SSE streaming
     - Design decisions
   - **Action**: **KEEP** - Historical record of conversation trace system
   - **Rationale**: Documents a completed feature, different from LiteLLM streaming

## Summary Table

| File | Size | Date | Status | Action |
|------|------|------|--------|--------|
| `streaming-architecture.md` | 17KB | Oct 6 | ✅ Current | **KEEP** |
| `streaming-rewrite-plan.md` | 33KB | Oct 5 | ✅ Implemented | **ARCHIVE** |
| `streaming_policy_architecture_plan.md` | 36KB | Sep 30 | ❌ Superseded | **DELETE** |
| `streaming_pipeline_instrumentation_plan.md` | 6.8KB | Oct 4 | ⚠️ Partial | **DELETE** |
| `conversation_trace/02_streaming_plan.md` | 2.5KB | Sep 27 | ✅ Different topic | **KEEP** |
| `conversation_trace/04_streaming_postmortem.md` | 5.9KB | Sep 27 | ✅ Historical | **KEEP** |

## Recommended Actions

### Immediate Actions

1. **DELETE outdated plans**:
   ```bash
   git rm dev/streaming_policy_architecture_plan.md
   git rm dev/streaming_pipeline_instrumentation_plan.md
   ```

2. **ARCHIVE the rewrite plan**:
   ```bash
   mkdir -p dev/archive
   git mv dev/streaming-rewrite-plan.md dev/archive/streaming-rewrite-plan.md
   ```

3. **UPDATE streaming-architecture.md** to reference the archived plan:
   ```markdown
   ## References
   - Implementation plan (completed): [dev/archive/streaming-rewrite-plan.md](./archive/streaming-rewrite-plan.md)
   ```

4. **ADD debugging section** to `streaming-architecture.md` (optional):
   - Document WebSocket logging (`get_websocket_logger()`)
   - Document chunk logging (`get_callback_chunk_logger()`)
   - Reference debug UI endpoints

### Future Maintenance

- **Single source of truth**: `dev/streaming-architecture.md` is the canonical reference
- **Archive completed plans**: Move implementation plans to `dev/archive/` once implemented
- **Delete superseded plans**: Don't keep outdated architectural proposals
- **Separate concerns**: Keep conversation trace docs separate from LiteLLM streaming docs

## Final State

After cleanup, we'll have:

```
dev/
├── streaming-architecture.md          # CANONICAL - current architecture
├── archive/
│   └── streaming-rewrite-plan.md      # Historical - implementation plan
├── conversation_trace/
│   ├── 02_streaming_plan.md           # Conversation trace streaming (different topic)
│   └── 04_streaming_postmortem.md     # Conversation trace postmortem
└── [other non-streaming docs]
```

## Rationale

**Why delete instead of archive?**

The superseded plans (`streaming_policy_architecture_plan.md`, `streaming_pipeline_instrumentation_plan.md`) represent:
- Exploration of ideas that were **not implemented** as written
- Implementation details that are **obsolete** (the actual code evolved differently)
- No historical value (we have git history if needed)

**Why archive the rewrite plan?**

The rewrite plan has value as:
- Design rationale (explains WHY we chose this approach)
- Implementation checklist (shows what was built)
- Problem analysis (documents the issues we solved)
- Future reference (if we need to revise the architecture)

**Why keep conversation trace docs?**

They document a **different streaming concern**:
- LiteLLM ↔ Control Plane streaming (proxy-side, WebSocket)
- Control Plane ↔ UI streaming (UI-side, SSE, conversation events)

These are orthogonal systems.

## Execution

To implement this plan:

```bash
# Create archive directory
mkdir -p dev/archive

# Archive the rewrite plan
git mv dev/streaming-rewrite-plan.md dev/archive/streaming-rewrite-plan.md

# Delete superseded plans
git rm dev/streaming_policy_architecture_plan.md
git rm dev/streaming_pipeline_instrumentation_plan.md

# Commit
git add -A
git commit -m "docs: consolidate streaming documentation

- Archive completed rewrite plan to dev/archive/
- Delete superseded architecture plans (pre-rewrite)
- Keep streaming-architecture.md as canonical reference
- Preserve conversation_trace/ docs (different concern)

See dev/STREAMING_DOCS_AUDIT.md for rationale."
```
