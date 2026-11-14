---
category: Fixes
pr: 829
---

**`/v1/{path}` passthrough is now an allowlist**: the catch-all forwards only the non-generation endpoints Claude Code needs (`POST /v1/messages/count_tokens`, `GET /v1/models`, `GET /v1/models/{id}`) and answers 404 for everything else, so generation requests can only go through `/v1/messages` and the policy pipeline
