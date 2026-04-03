---
category: Refactors
---

**Remove prefix-based credential type heuristic**: The gateway now relies solely on the transport header (`Authorization: Bearer` vs `x-api-key`) to determine credential type. Removed `is_anthropic_api_key()` and the `oauth_via_api_key` credential type that second-guessed the transport header using token prefix inspection.
