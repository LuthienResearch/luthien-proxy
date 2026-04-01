---
category: Fixes
---

**Persist ADMIN_API_KEY to .env during local onboard**: `luthien onboard` now writes `ADMIN_API_KEY` to the gateway `.env` file, preventing auth failures after gateway restart caused by the gateway generating a new random key on each startup.
