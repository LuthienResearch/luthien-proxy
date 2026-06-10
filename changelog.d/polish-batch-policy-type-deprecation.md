---
category: Refactors
pr: 793
---

**Batch policy_type deprecation**: Replaced the per-row deprecation loop in built-in policy type sync with a single `NOT IN` UPDATE (N+1 round trips → 1)
  - Test assertions now derive the expected count from `len(REGISTERED_BUILTINS)` instead of a hardcoded `18`, preventing future assertion drift
