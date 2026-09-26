---
category: Features
---

**Passthrough OpenAI/Gemini calls now materialize into conversation history, search, and export**
  - Previously the `/openai/*` and `/gemini/*` passthrough routes only wrote
    raw `request_logs`; those calls never appeared in `/api/history`,
    `/api/debug/calls`, session summaries, FTS, or the JSONL export, and were
    unreadable by downstream tooling (cybertasks).
  - Adds a `passthrough_materialize` package that normalizes captured OpenAI
    (chat + Responses, buffered + streamed) and Gemini (generateContent +
    streamGenerateContent) payloads into the canonical Anthropic-shaped
    conversation-event contract while preserving the exact provider-native
    request/response verbatim for faithful reprobe.
  - Materialization is idempotent (advisory lock + request-event existence
    guard, single transaction, two session-summary updates) and driven two
    ways: a live post-commit callback on `RequestLogRecorder` (gated by
    `PASSTHROUGH_MATERIALIZE_ENABLED`) and a dashboard-only reconcile worker +
    one-shot backfill CLI (gated by `PASSTHROUGH_MATERIALIZE_BACKFILL_ENABLED`)
    for historical rows.
  - Passthrough requests now carry user attribution via the same trusted
    `X-Luthien-User-Id` / Bearer-JWT policy as the Anthropic path. Malformed or
    unsupported eligible payloads fail loudly and stay retryable; no partial
    rows are ever written. Existing read paths light up with zero new
    migrations and SQLite/Postgres parity.
