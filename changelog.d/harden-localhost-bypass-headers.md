---
category: Fixes
pr: 830
---

**Localhost bypass hardening**: more forwarding headers now turn the localhost auth bypass off. The denylist adds `Via`, the CDN client-IP headers `CF-Connecting-IP`, `True-Client-IP` and `Fastly-Client-IP`, and `X-Client-IP` and `X-Cluster-Client-IP` from load balancers and frameworks
  - Any `X-Forwarded-*` or `X-Original-*` header now counts, so new headers in those families don't need a list update
