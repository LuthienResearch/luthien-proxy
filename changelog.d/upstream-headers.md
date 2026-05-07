---
category: Features
pr: 716
---

**Configurable upstream header injection**: New `UPSTREAM_HEADERS` environment variable (JSON) lets the operator inject custom headers into upstream LLM API requests with per-request template expansion (`${session_id}`, `${request_path}`, `${env.VARNAME}`). Primary use case: chaining Luthien in front of Helicone or other LLM observability proxies that require session tracking and analytics headers. Misconfiguration (invalid JSON, malformed RFC 7230 header names, hop-by-hop headers, non-string values) fails the gateway at startup rather than silently disabling the integration. Replaces the previously rejected PR #549; original commits authored by sjawhar are preserved.
