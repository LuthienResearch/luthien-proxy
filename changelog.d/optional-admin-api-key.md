---
category: Fixes
pr: 405
---

**Optional ADMIN_API_KEY**: Gateway no longer crashes on startup when `ADMIN_API_KEY` is unset — admin endpoints handle the missing key gracefully at request time instead.
