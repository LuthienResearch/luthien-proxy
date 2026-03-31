---
category: Refactors
pr: 483
---

**Remove dead match entry in parse_judge_response**: Remove unreachable `"```json"` from the prefix match set — `lstrip("`")` already strips all backticks before the check.
