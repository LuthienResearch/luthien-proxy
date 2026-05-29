---
category: Fixes
---

**`derive_builtin_name` splits acronym-then-word boundaries**: a policy class
name like `HTTPSRedirectPolicy` now derives to `https-redirect` instead of
`httpsredirect`. No-op for current `REGISTERED_BUILTINS`; only affects future
acronym-prefixed names.
