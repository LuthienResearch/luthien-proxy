---
category: Fixes
---

**Webhook payloads now include thinking for streamed calls**: after #815, the rebuilt streamed response that webhooks receive includes `thinking` text and signatures (and `redacted_thinking` data), matching non-streaming calls. Previously these were excluded from streamed webhook payloads. Webhook receivers that store or forward payloads should expect the extra content.
