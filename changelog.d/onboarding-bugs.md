---
category: Fixes
pr: 385
---

**Fix local onboarding and Docker port conflicts**: Install `luthien-proxy` from GitHub instead of PyPI (not yet published). `luthien up` in Docker mode now auto-selects free ports for conflicting services and saves the resolved gateway URL to config so `luthien claude` routes correctly.
