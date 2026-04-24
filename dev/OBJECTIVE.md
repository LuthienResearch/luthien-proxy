# Credentials UI Discoverability (PR 1 of 5)

First PR in a 5-PR initiative to add server-side inference provider support.
This PR focuses purely on UI discoverability — no schema changes, no provider
concepts, no judge client work. Later PRs handle those.

## Scope

1. Add `/credentials` link to the global nav (`static/nav.js`).
2. Add a link/card on the `/config` dashboard pointing to `/credentials` so
   operators see where server-side API credentials are managed.
3. Audit `static/credentials.html` against the Server Credentials admin API
   (`POST|GET|DELETE /api/admin/credentials`) and surface the CRUD operations.
   The existing page manages auth-mode config + cached user credentials but
   never exposed the server-credential CRUD endpoints that already exist.

## Acceptance

- Nav shows a Credentials link on every page that uses `nav.js`.
- `/config` has a visible pointer to `/credentials`.
- On `/credentials` an operator can list, create, and delete server
  credentials (name + value + credential_type + platform + platform_url)
  via the existing admin API, with clear validation and error messages.

## Out of scope (later PRs)

- Renaming `auth_provider` to inference-provider terminology.
- Adding provider concepts to policy YAML.
- Judge client refactoring.
- OAuth token management UI flows beyond the generic `auth_token` type.
