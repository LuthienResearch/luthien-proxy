---
category: Features
pr: 549
---

**Configurable upstream header injection**: New `UPSTREAM_HEADERS` environment variable (JSON) lets you inject custom headers into upstream API requests with per-request template expansion (`${session_id}`, `${request_path}`, `${env.VARNAME}`). Primary use case: chaining Luthien in front of Helicone or other LLM observability proxies that require session tracking and analytics headers.
