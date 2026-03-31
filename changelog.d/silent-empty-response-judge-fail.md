---
category: Fixes
pr: 451
---

**Inject error message when judge failure silently strips all content**: When `on_error: block` is configured and the safety judge fails (auth error, network, rate limit), all content blocks were previously dropped with no explanation — the gateway returned an empty response with `stop_reason: end_turn` and Claude Code showed "Cogitated for Xs" then nothing. The gateway now injects an error text block in both the non-streaming and streaming paths explaining that the response was blocked due to a judge failure.
