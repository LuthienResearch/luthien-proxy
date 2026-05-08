---
category: Features
---

**Before/After preview backend for the admin policy-test endpoint**:
The admin `/api/admin/test/chat` endpoint now returns both `before_content`
(raw LLM output for the original request) and `content` (the active policy's
output for that same exchange). Operators can use the diff to verify a
policy does what they think before activating it on real traffic.
  - The endpoint orchestrates the LLM call and policy hooks in-process and
    no longer makes an HTTP roundtrip to `/v1/messages`. The gateway request
    pipeline is untouched and there is no client-facing protocol opt-in.
  - `body.api_key`, when supplied, is now sent directly to Anthropic
    (previously it was used to authenticate against the local proxy).
  - Streaming-only policies appear as no-ops in this preview by design —
    the non-streaming hooks are the source of truth for the test path.
