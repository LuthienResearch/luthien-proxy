---
category: Fixes
---

**History session previews now reflect user input, not gateway-injected content**:
  `_extract_preview_message` now reads from `original_request` instead of
  `final_request`, so previews show the actual user message even when
  `inject_policy_awareness_anthropic` (or any future injector) prepends content
  to the first user message. Falls back to `final_request` for older payloads.
