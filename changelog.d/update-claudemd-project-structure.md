---
category: Chores & Docs
pr: 523
---

**Update CLAUDE.md project structure**: Rewrote the `src/luthien_proxy/` module map to match the actual layout — removed stale `orchestration/` and `streaming/` entries (replaced by `pipeline/`), added missing subpackages (`pipeline/`, `request_log/`, `history/`, `usage_telemetry/`, `credentials/`, `static/`) and key top-level modules (`auth.py`, `session.py`, `credential_manager.py`, `policy_composition.py`, `policy_manager.py`, `gateway_routes.py`, `dependencies.py`, `main.py`, `config.py`, `telemetry.py`, `config_fields.py`, `config_registry.py`). Also added admin/auth/policy env vars (`AUTH_MODE`, `ADMIN_API_KEY`, `LOCALHOST_AUTH_BYPASS`, `POLICY_SOURCE`) to the Environment Setup section.
