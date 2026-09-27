---
category: Features
pr: 797
---

**Opt-in passthrough fallback** (`PASSTHROUGH_FALLBACK_ENABLED`, default off): when a policy-modified request is rejected upstream with a request-shaped 4xx (400/404/413/422), the gateway can retry once with the original, pre-policy request
  - Double opt-in: the flag must be on AND the active policy must declare its request edits safe to lose (`passthrough_fallback_safe = True` on the policy class; default `False`). In a policy chain every sub-policy must opt in. No shipped request-rewriting policy opts in, so the fallback never undoes a redaction, model restriction, or other request-side safety edit
  - Fail-open when it fires: the policy's request edits are discarded for that request. Response-side policy behavior (response rewrites, blocks) still applies to the fallback response
  - Fires only when the policy actually changed the request; streaming falls back only before any backend event arrived
  - Observable: WARNING log, a `pipeline.passthrough_fallback` event, and `transaction.request_recorded` names the original as the request actually sent, with a `passthrough_fallback` block holding the rejected request and the upstream status and message
  - Runtime-settable: anyone with the admin API key can toggle the flag at runtime via the admin config API (no restart)
