---
category: Fixes
pr: 313
---

**Sanitize client-facing error detail leakage**: Replace raw `str(e)` exception messages in HTTP responses with generic messages across pipeline, admin, and history routes. Internal details (Pydantic traces, DB errors, module paths) are now logged server-side with `repr(e)` only and never forwarded to clients. Set `VERBOSE_CLIENT_ERRORS=true` to restore verbose error details for local debugging.
