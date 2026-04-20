---
category: Features
pr: 595
---

**Week 0 community PR integration**: Merged 10 PRs from a community contributor fork
  - OTEL HTTP instrumentation now enabled by default
  - Role-based access control (RBAC) for admin endpoints
  - StringReplacementPolicy for request filtering
  - History preview endpoint fix
  - Health and readiness check endpoints (`/health`, `/ready`)
  - Upstream header injection for request context
  - User identity extraction from credentials
  - Data retention policies with S3 archival support
  - Webhook event export for external integrations
  - Server-side session search with full-text indexing

**Breaking change**: Admin UI pages (`/history`, `/activity`, `/policies`, etc.) now redirect to `/login?error=not_configured` when `ADMIN_API_KEY` is unset and `LOCALHOST_AUTH_BYPASS=false`. Previously they were accessible without authentication. Operators running without `ADMIN_API_KEY` must either set it or enable `LOCALHOST_AUTH_BYPASS=true` (default for local dev).
