---
category: Fixes
pr: 806
---

**SQLite timestamps**: Store `datetime` bind parameters as ISO-8601 with a `T` separator so `created_at` range filters compare correctly on SQLite. Previously a `datetime` bind was stored with a space separator (stdlib `sqlite3`'s legacy adapter), which sorts before the `T` form used by `.isoformat()` comparisons, silently breaking session-search time-range filters and (had it shipped without the accompanying migration) retention cutoffs against existing data. A migration backfills existing `conversation_calls`/`conversation_events`/`session_summaries` timestamps to the `T` form so old and new rows compare consistently.
