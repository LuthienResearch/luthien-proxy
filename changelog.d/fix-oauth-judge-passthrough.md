---
category: Fixes
---

**Fix OAuth token routing in judge policy LLM calls**: OAuth bearer tokens passed through to judge LLM calls were silently sent via the wrong header (`x-api-key` instead of `Authorization: Bearer`), causing authentication failures. Judge calls now detect bearer tokens from the original request transport header and route them correctly via `extra_headers`.
