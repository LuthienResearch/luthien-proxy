---
category: Features
pr: 797
---

**Opt-in passthrough fallback** (`PASSTHROUGH_FALLBACK_ENABLED`, default off): when a policy-modified request is rejected upstream with a request-shaped 4xx (400/404/413/422), the gateway retries once with the original unmodified request so the proxy is never worse than direct API access
  - Fires only when the policy actually changed the request; streaming falls back only before any backend event arrived
  - Observable: emits a `pipeline.passthrough_fallback` event and a WARNING log when it fires — policy failures are never silently masked
  - Intentional policy blocks are unaffected: blocks are policy-layer decisions and never surface as upstream errors, so the fallback structurally cannot override them
