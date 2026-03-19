---
category: Features
pr: 351
---

**Remove OpenAI gateway and Codex support**: The proxy now exclusively supports the Anthropic `/v1/messages` endpoint. Removed `/v1/chat/completions`, LiteLLM request routing, and Codex CLI support. LiteLLM is retained only for policy-internal judge LLM calls.
