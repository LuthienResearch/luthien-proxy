---
category: Fixes
pr: 534
---

**Enum defaults in generated `.env.example`**: `AuthMode(str, Enum)` fields rendered as `AuthMode.BOTH` instead of `both` because `Enum.__str__` wins over `str.__str__`. The generator now explicitly unwraps `Enum` defaults via `.value`, and a regression test guards the behavior.
