---
category: Fixes
pr: 476
---

**PROXY_API_KEY no longer required**: The proxy no longer requires `PROXY_API_KEY` to be set — `AUTH_MODE=both` degrades gracefully to passthrough-only. Neither `luthien onboard` nor `luthien hackathon` generate a proxy key. `AUTH_MODE=proxy_key` without a key is now a hard startup error.
