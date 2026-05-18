---
category: Chores & Docs
---

**Delete stale/unreferenced `dev/*.md` files, move accurate architecture doc to `dev/context/`**: Removed `dev/LIVE_POLICY_DEMO.md`, `dev/OBSERVABILITY_DEMO.md`, `dev/observability.md`, `dev/VIEWING_TRACES_GUIDE.md`, `dev/success.md`, `dev/plans/*.md`, and `dev/user-stories/` (all stale, superseded, or no longer in use). Moved `dev/REQUEST_PROCESSING_ARCHITECTURE.md` → `dev/context/request_processing.md` — it remains accurate and belongs with other developer-internals docs. Updated `dev-README.md` cross-references accordingly. Leaves `dev/` root clean with only the three documented subdirs (`scratch/`, `context/`, `archive/`).
