---
category: Features
pr: 795
---

**History detail performance**: bound memory and make the conversation-history detail view fast for arbitrarily large sessions (fixes admin-dashboard slowness/OOM on large sessions).
  - List reads precomputed `session_summaries` (no full-payload scan); one-time preview backfill.
  - Detail + markdown/JSONL exports stream bounded-memory; O(turns) single-anchor request reconstruction (the largest cumulative request array is parsed once and per-turn deltas are sliced from it via a raw→parsed prefix map), with a fast path plus a correctness fallback for request-modified / non-monotonic sessions. Output is byte-identical to the per-turn build.
  - Conversation-detail turn pagination (`offset`/`limit`; omit `offset` ⇒ newest page): only the requested window's payloads are read, so each request is bounded. Frontend loads the newest page first and loads older pages on scroll-up; whole-session stats stay invariant across pages; rendered conversation unchanged. This is the conversation-page lazy-loading deferred by #752.
