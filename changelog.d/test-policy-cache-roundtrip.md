---
category: Chores & Docs
---

**Policy cache round-trip test coverage**: Expanded `PolicyCache` unit tests to cover non-trivial round-trip values — deeply nested dicts/lists, BMP and supplementary-plane unicode (including emoji, ZWJ sequences, combining characters), large payloads (~100KB, including multi-byte), JSON control characters, scalar top-level values, type-preservation for bool vs int, empty containers, None, integer and float edge values, type-changing overwrites, unicode dict keys and cache keys, and policy-name isolation with unicode namespaces. Follow-up to PR #521 review item #11.
