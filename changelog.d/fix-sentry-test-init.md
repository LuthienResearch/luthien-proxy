---
category: Fixes
pr: 486
---

**Prevent Sentry initialization during test runs**: litellm's `load_dotenv()` was picking up `SENTRY_ENABLED=true` from the repo's `.env` before the test guard could run, causing test exceptions to be sent to production Sentry. Moved the guard to module-level in `tests/conftest.py` with force-set instead of `setdefault`.
