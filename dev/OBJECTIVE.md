# Objective: Inference provider registry (DB + admin API + UI)

PR #3 of a 5-PR inference-provider initiative. Stacked on PR #605.

## Scope

Build a named registry for `InferenceProvider` instances, mirroring the
operator workflow we already have for server credentials: DB-backed,
admin-API surface, dedicated UI page.

Concretely:
- New `inference_providers` DB table (dual postgres/sqlite migrations, #014).
- `InferenceProviderRegistry` class with TTL cache + close-lifecycle.
- Admin API: POST / GET / DELETE `/api/admin/inference-providers`.
- `/inference-providers` UI page matching `credentials.html` style.
- Nav entry + config dashboard pointer card + discoverability link from
  `/credentials`.
- Unit tests and one sqlite_e2e round-trip test.

## Out of scope

- Policy YAML rename `auth_provider:` → `inference_provider:` (PR #4).
- Migrating judge policies to use the registry (PR #4).
- `/ping` endpoint on providers (PR #5).

## Acceptance

- Can create, list, delete providers via admin API against a running
  gateway.
- `/inference-providers` page loads, lists providers, creates via form,
  deletes with confirmation.
- `dev_checks.sh` is clean.
- Changelog fragment added.
