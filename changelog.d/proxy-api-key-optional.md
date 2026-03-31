---
category: Fixes
pr: 476
---

**PROXY_API_KEY no longer required**: The proxy no longer requires `PROXY_API_KEY` to be set — passthrough auth mode works without it. Local onboard no longer generates or writes a proxy key to `.env`, and `ADMIN_API_KEY` is now properly persisted.
