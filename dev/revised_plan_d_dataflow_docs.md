# Revised Plan D: Dataflow Documentation Consolidation

**Date:** 2025-10-10
**Status:** ✅ COMPLETED (2025-10-10)

## Problem Statement

We have redundant dataflow documentation across three files (813 lines total):
- `docs/dataflows.md` (322 lines) - Mix of WHY, WHAT, and HOW with outdated line refs
- `docs/dataflow-diagrams.md` (242 lines) - Visual diagrams (accurate)
- `docs/reading-guide.md` (249 lines) - Onboarding with duplicate diagrams

**Issues found:**
- Mermaid diagrams duplicated in reading-guide.md and dataflow-diagrams.md
- Hook flow explanations duplicated (text in dataflows.md, visual in diagrams)
- Outdated line references (e.g., dataflows.md:26 says callback is at :172-189, actually :173-190)
- Broken link to non-existent data-storage.md (dataflows.md:118)
- Incorrect docstring (streaming_routes.py:236 claimed conversation_events writes that don't happen) - **FIXED**

## Proposed Solution

**Three focused docs with clear purposes:**

### 1. `docs/ARCHITECTURE.md` (~130 lines, NEW)

**Purpose:** The "WHY" document - architectural decisions and component overview

**Content sources (from dataflows.md):**
- Lines 3-6: Executive summary
- Lines 7-19: Architecture diagram + component list
- Lines 204-227: Component details (file paths, config, schema refs)
- Lines 228-249: Architectural decisions & rationale
- Lines 251-269: Data retention policies
- Lines 116-148: Data Storage section (condensed to ~15 lines)
  - List Postgres tables with one-line purposes
  - Note Redis channels
  - Link to prisma/schema.prisma for details
  - Keep essential event JSON snippet if space allows
- NEW: Cross-links section pointing to:
  - `src/luthien_proxy/policies/` for policy implementations
  - `prisma/*/schema.prisma` for database schemas
  - Observability docs/scripts

**What we're NOT including (deleted from dataflows.md):**
- Lines 21-115: Hook flow text descriptions (moved to developer-onboarding.md)
- Lines 149-183: Policy API examples (moved to developer-onboarding.md)
- Lines 271-321: Sequence diagram (MOVED to diagrams.md, not deleted - provides unique timeline perspective)

### 2. `docs/diagrams.md` (~290 lines, RENAMED from dataflow-diagrams.md)

**Purpose:** Visual reference - single source of truth for all diagrams

**Changes:**
- Rename file: `dataflow-diagrams.md` → `diagrams.md`
- **Add sequence diagram** from dataflows.md:271-321 (~48 lines)
  - Provides timeline-style view of hook coordination between LiteLLM, control plane, and backend
  - Complements existing flowchart diagrams with call-order perspective
- No other content changes (existing diagrams already accurate)

### 3. `docs/developer-onboarding.md` (~220 lines, RENAMED from reading-guide.md)

**Purpose:** "New to Luthien? Start here" - structured learning path

**Note:** Size increased from initial ~160 estimate to ~220 after accounting for full hook flow narratives, streaming protocol details, and policy API content. Still manageable with clear sectioning and TL;DR at top.

**Content sources:**
- From reading-guide.md:
  - Step-by-step code reading path
  - Deep dives section
  - FAQ
  - Data structures reference
- From dataflows.md:
  - Lines 21-115: Hook Flows narrative (all 4 hooks with step-by-step text)
  - Lines 62-107: Streaming protocol details + provider normalization
  - Lines 149-183: Policy API section (including ToolCallBufferPolicy example)
  - Lines 184-202: Observability commands (condensed to essentials)

**Content removed (from current reading-guide.md):**
- Lines 10-93: Inline Mermaid diagrams (non-streaming flow)
- Lines 52-93: Inline Mermaid diagrams (streaming flow)
- Lines 99-128: Inline result handling diagram

**Content replaced with:**
```markdown
## Visual Overview

👉 **Start with diagrams:** See [diagrams.md](diagrams.md) for visual flows:
- [Non-Streaming Request Flow](diagrams.md#non-streaming-request-flow)
- [Streaming Request Flow](diagrams.md#streaming-request-flow)
- [Result Handling Pattern](diagrams.md#result-handling-pattern)

Once you've reviewed the diagrams, follow the code reading path below...
```

### 4. `docs/dataflows.md` - DELETED

**Content redistributed to:**
- ARCHITECTURE.md: Executive summary, component details, architectural decisions, data retention, condensed data storage
- developer-onboarding.md: Hook flows, policy examples, observability commands
- diagrams.md: Sequence diagram (provides unique timeline perspective)
- Deleted entirely: Only duplicated storage details (schema.prisma is source of truth)

## Content Mapping Verification

**Nothing valuable is lost:**
- ✅ Executive summary → ARCHITECTURE.md
- ✅ Architecture overview → ARCHITECTURE.md
- ✅ Hook flows (text) → developer-onboarding.md
- ✅ Streaming protocol → developer-onboarding.md
- ✅ Provider normalization → developer-onboarding.md
- ✅ Data storage overview → ARCHITECTURE.md (condensed)
- ✅ Policy API examples → developer-onboarding.md
- ✅ ToolCallBufferPolicy example → developer-onboarding.md
- ✅ Observability commands → developer-onboarding.md (condensed)
- ✅ Component details → ARCHITECTURE.md
- ✅ Architectural decisions → ARCHITECTURE.md
- ✅ Data retention → ARCHITECTURE.md
- ✅ Visual diagrams → diagrams.md (already there)
- ✅ Sequence diagram → diagrams.md (MOVED, provides unique timeline perspective)
- ❌ Duplicated storage details → DELETED (schema.prisma is source of truth)

## File Purposes Summary

| File | Audience | Question Answered | Lines |
|------|----------|-------------------|-------|
| `ARCHITECTURE.md` | Experienced devs, reviewers | "Why is it built this way?" | ~130 |
| `diagrams.md` | Everyone | "Show me how it works visually" | ~290 |
| `developer-onboarding.md` | New developers | "How do I learn this codebase?" | ~220 |

**Total: ~640 lines** (down from 813 lines, **21% reduction**)

**Why still worthwhile despite smaller reduction:**
- Eliminates duplication (diagrams, hook flows)
- Clear separation of concerns (WHY vs VISUAL vs HOW-TO-LEARN)
- Single source of truth for all diagrams
- Improved legibility through focused documents

## Implementation Steps

### Phase 1: Fix Critical Code Issues (COMPLETED)

- [x] Fix streaming_routes.py:236 docstring (COMPLETED)
- [x] Fix dataflows.md line refs for callbacks (COMPLETED)
- [x] Remove broken link to data-storage.md (COMPLETED)

**Note:** Skipping additional dataflows.md line ref fixes since that file is being deleted. Will ensure refs are correct in NEW docs during Phase 2.

### Phase 2: Execute Consolidation

1. Create `docs/ARCHITECTURE.md` with content from dataflows.md (including condensed data storage with JSON snippet)
2. Create `docs/diagrams.md`:
   - Rename from `dataflow-diagrams.md`
   - Add sequence diagram from dataflows.md:271-321
3. Create `docs/developer-onboarding.md`:
   - Transform from `reading-guide.md`
   - Add hook flows, streaming protocol, policy API from dataflows.md
   - Remove inline diagrams, link to diagrams.md instead
   - Add TL;DR at top for navigation
4. Delete `docs/dataflows.md`
5. Update all cross-references:
   - Search codebase for links to old filenames
   - Update README.md
   - Update dev/dataflow_legibility_plan.md
   - Update tests/e2e_tests/CLAUDE.md

## Codex Review Feedback (Incorporated)

**Key changes from initial draft:**

1. ✅ **Keep sequence diagram** - Move to diagrams.md instead of deleting (provides unique timeline view)
2. ✅ **Realistic size estimates** - Increased developer-onboarding.md from 160 to 220 lines
3. ✅ **Keep JSON event example** - In ARCHITECTURE.md data storage section for quick reference
4. ✅ **Skip dataflows.md line fixes** - File is being deleted, fix refs in new docs only
5. ✅ **Clear sectioning** - Add TL;DR to developer-onboarding.md for navigation

**Final verdict:** Plan approved by Codex with above adjustments

## Success Metrics

**Before:**
- 813 lines across 3 docs with significant duplication
- Outdated line references
- Unclear separation of concerns (WHY vs WHAT vs HOW)
- Diagrams duplicated in multiple files

**After:**
- ~640 lines across 3 docs (21% reduction)
- Accurate line references
- Clear purposes: ARCHITECTURE (why), diagrams (visual), developer-onboarding (how to learn)
- Single source of truth for diagrams (including sequence diagram)
- No loss of valuable content (JSON examples preserved, all narratives kept)

## Codex Answers to Open Questions

1. **Data Storage section**: ~15 lines with JSON event example - provides quick reference without forcing developers to hunt through schema files ✅

2. **ToolCallBufferPolicy example**: Yes, in developer-onboarding.md Deep Dives section (learning material, not architectural rationale) ✅

3. **Hook Flows narrative**: Yes, in developer-onboarding.md (part of "how to learn the codebase") ✅

4. **Line reference format**: Use specific function definition line (e.g., `:385`) - stays stable after minor edits ✅

5. **Cross-link format**: File-level pointers sufficient; only add line ranges for truly critical callouts ✅
