---
category: Fixes
pr: 386
---

**Fix session ID not recorded in OAuth passthrough mode**: Fall back to `x-session-id` header when `metadata.user_id` is absent or doesn't match the API key session format. Conversation history is now recorded for OAuth users.
