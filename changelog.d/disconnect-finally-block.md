---
category: Fixes
pr: 466
---

**Fix finally block on client disconnect**: When a streaming client disconnects (either via explicit `is_disconnected()` detection or Starlette's ASGI `GeneratorExit`/`CancelledError` path), the `finally` block now correctly skips recording a partial response to conversation history, logging a success status, and counting the request as a completed streaming call. `GeneratorExit` and `CancelledError` are caught, `final_status` is set to 499, and the `client_disconnected` flag is set before re-raising — ensuring all existing guards in the `finally` block apply to both disconnect paths.
