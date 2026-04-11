---
category: Chores & Docs
---

**Fix flaky `test_new_key_is_never_immediately_self_evicted`**: The test used `ttl_seconds=1` to demonstrate "short TTL" but SQLite stores `expires_at` at second precision (`datetime('now')` truncates fractional seconds), so up to ~1 full second of the TTL could vanish before the assertion. On loaded CI runners the entry would already be expired when the test asserted `cache.get("c_new") == {"v": "new"}`, producing an `AssertionError: assert None == {'v': 'new'}`. Raised the TTL to 60s — still much shorter than the 10,000s of the other rows (which is what the test actually needs to exercise FIFO-vs-expires_at ordering), and now comfortably above any realistic put→get latency.
