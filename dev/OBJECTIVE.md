# Objective: Auto-Discovering Policy Configuration UI

## Goal
Replace hardcoded policy list with auto-discovery and fix the broken policy configuration UI.

## Acceptance Criteria
- [ ] `/admin/policy/list` auto-discovers all policies from `src/luthien_proxy/policies/`
- [ ] Config schema extracted from constructor signatures
- [ ] UI allows selecting any policy and editing its config
- [ ] UI uses existing `/admin/policy/set` endpoint (not broken create/activate)
- [ ] Unit tests for discovery module
- [ ] dev_checks.sh passes
