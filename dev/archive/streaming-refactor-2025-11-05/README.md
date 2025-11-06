# Streaming Refactor Archive - November 5, 2025

This directory contains documentation from the completed streaming pipeline refactor.

## What Was Accomplished

The streaming pipeline was refactored from an implicit callback-based architecture to an explicit queue-based architecture with dependency injection. See [../../success.md](../../success.md#2025-11-05-streaming-pipeline-refactor-complete) for full details.

**Summary**:
- Simplified from 3 stages to 2 stages (removed CommonFormatter)
- Added 67 unit tests (55 PolicyExecutor, 12 ClientFormatter)
- All 309 existing tests passing
- ~200 lines of unnecessary code eliminated
- PolicyOrchestrator simplified to ~30 lines

## Archived Files

### Completed Work
- **OBJECTIVE-completed.md** - Original objective and success criteria (all ✅)
- **NOTES-completed.md** - Implementation notes and design evolution

### Obsolete Design Docs
- **hardcoded_constants_audit.md** - Audit that referenced old `streaming_orchestrator.py` (critical issues fixed, file references deleted architecture)
- **simplified_aggregation_design.md** - Old `ToolCallStreamGate` design (replaced by `StreamingChunkAssembler`)
- **stream_processor_api.md** - Old streaming API design (replaced by PolicyExecutor/ClientFormatter)
- **streaming_patterns.md** - Empirical streaming chunk patterns (data still useful, but design discussion outdated)
- **observability-architecture-proposal.md** - OTel proposal (implemented, now canonical in observability-v2.md)
- **observability_review_summary.md** - Historical review before implementation

## Current Documentation

The refactor is now documented in:

### Primary Reference
- **[../../STREAMING_ARCHITECTURE.md](../../STREAMING_ARCHITECTURE.md)** - Comprehensive architecture overview with diagrams and request flow stories

### Context Files
- **[../../context/codebase_learnings.md](../../context/codebase_learnings.md#streaming-pipeline-architecture-2025-11-05)** - Architecture patterns and key principles
- **[../../context/decisions.md](../../context/decisions.md#streaming-pipeline-queue-based-architecture-2025-11-05)** - Decision rationale and trade-offs
- **[../../context/gotchas.md](../../context/gotchas.md)** - Queue patterns and common pitfalls

### Implementation
- `src/luthien_proxy/v2/orchestration/policy_orchestrator.py` - Simplified orchestrator (~30 lines)
- `src/luthien_proxy/v2/streaming/policy_executor/` - Block assembly + policy hooks
- `src/luthien_proxy/v2/streaming/client_formatter/` - SSE conversion

## Why These Were Archived

All files in this archive either:
1. Document completed work (OBJECTIVE, NOTES)
2. Reference architecture that no longer exists (streaming_orchestrator.py, ToolCallStreamGate)
3. Were superseded by canonical documentation (observability proposals)

The valuable insights from these documents have been extracted and integrated into the current documentation.
