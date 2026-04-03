---
category: Fixes
pr: 482
---

**Sanitize Redis URL in logs**: Strip credentials from Redis connection URL before logging to prevent credential exposure.
