# Objective

Update the project-root `CLAUDE.md` "Project Structure & Module Organization" section to match the actual layout of `src/luthien_proxy/`, and document the admin/auth/policy env vars that agents need to know about.

## Scope (CLAUDE.md only)

- Remove stale module entries (`orchestration/`, `streaming/`) that no longer exist.
- Add missing subpackages: `pipeline/`, `request_log/`, `history/`, `usage_telemetry/`, `credentials/`.
- Add key top-level modules agents will touch: `auth.py`, `credential_manager.py`, `policy_composition.py`, `policy_manager.py`, `gateway_routes.py`, `dependencies.py`.
- Add admin/auth/policy env vars to the Environment Setup section: `AUTH_MODE`, `ADMIN_API_KEY`, `POLICY_SOURCE`, `LOCALHOST_AUTH_BYPASS`.
- Verify every remaining claim against source before keeping it.

## Out of scope (separate cards)

- `ARCHITECTURE.md` rewrite — tracked in card 69d85b64.
- `dev/context/` LiteLLM cleanup — tracked in card 69d85b73.
- Admin auth docs correctness — tracked in card 69d85b85.

## Acceptance check

- Every module listed under `src/luthien_proxy/` in CLAUDE.md exists on disk.
- Every module that exists on disk and is non-trivial for agents is listed.
- Env vars listed in "Environment Setup" match what `.env.example`/`config_fields.py` define for auth and policy.
