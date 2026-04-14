---
category: Fixes
---

**Admin auth documentation correctness**: Corrected `dev-README.md`, `dev/context/codebase_learnings.md`, and `dev/context/gotchas.md` which all claimed admin endpoints unconditionally required `Authorization: Bearer ADMIN_API_KEY`. In reality, `LOCALHOST_AUTH_BYPASS` (enabled by default) covers admin routes too — this was intentional (PR #405) but the docs were never updated. Also clarified the `LOCALHOST_AUTH_BYPASS` config description and enumerated the accepted admin credentials (Bearer, `x-api-key`, session cookie).
