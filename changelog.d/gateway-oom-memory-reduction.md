---
category: Fixes
pr: 453
---

**Reduce gateway memory footprint to fit 1G Docker limit**: Skip duplicate raw-event buffering during streaming, make client cache size configurable via `ANTHROPIC_CLIENT_CACHE_SIZE`, and validate `LOG_LEVEL` at startup.
