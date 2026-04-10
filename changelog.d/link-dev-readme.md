---
category: Chores & Docs
pr: 514
---

**Consolidate dev docs**: Make `dev-README.md` the canonical development guide, deduplicate `CLAUDE.md`, delete the stale `dev/README.md` navigation index, and rewrite the releasing section to document the auto-tag workflow. Also fixes several inaccuracies surfaced during review (observability defaults, auth layers, deployment modes, e2e commands, billing warning).

**Fix stale SERVICE_VERSION**: `service_version` now derives from `luthien_proxy.version.PROXY_VERSION` (package metadata) instead of a hardcoded `"2.0.0"` relic. **Operational note**: Sentry `release` tags and OTel `service.version` resource attributes will change shape from `luthien-proxy@2.0.0` to the actual package version (e.g. `0.1.20.dev2+g64a517c2`). Dashboards or alerts filtering on the old value will need to be updated.
