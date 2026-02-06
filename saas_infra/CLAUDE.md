# saas_infra — Railway Provisioning for Luthien Proxy

## What This Module Does

Provisions isolated luthien-proxy instances on Railway. Each instance is a full Railway project containing Postgres, Redis, and a gateway service deployed from the luthien-proxy GitHub repo. The CLI (`python -m saas_infra.cli`) exposes create/list/status/delete/redeploy commands.

## Architecture: Hybrid CLI + GraphQL

This is the single most important thing to understand. Railway operations are split across two execution paths:

**Railway CLI (subprocess)** — for mutations that trigger state transitions:
- `railway init` (create project)
- `railway add -d postgres/redis` (add database from template)
- `railway add --service gateway --repo ...` (add service from GitHub)
- `railway variables --set "K=V"` (set env vars)
- `railway domain` (generate domain)

**Railway GraphQL API (httpx)** — for reads and simple mutations:
- `list_projects()`, `get_project()`, `find_project_by_name()`
- `list_luthien_instances()`, `get_instance()`
- `update_project_description()` (used for soft-delete tagging)
- `delete_project()`, `trigger_deployment()`

**Why not all GraphQL?** Raw GraphQL mutations cause "endpoint too recently updated" errors because Railway services go through internal state transitions (creating -> configuring -> ready). The CLI waits for these transitions internally. GraphQL returns immediately after the initial mutation, so subsequent mutations hit the service while it's still transitioning.

**Why not all CLI?** The CLI has no `list` command with project details, no way to query service IDs, and its output format is inconsistent. GraphQL is reliable and fast for reads.

## Module Layout

```
saas_infra/
  cli.py             — Click CLI commands (create, list, status, delete, cancel-delete, redeploy, whoami)
  provisioner.py      — Orchestrates the create flow (calls railway_client methods in sequence)
  railway_client.py   — Both GraphQL (_execute) and CLI (_run_cli) execution, all Railway operations
  models.py           — Data classes (InstanceInfo, ServiceInfo, ProvisioningResult, enums)
  utils.py            — Name validation, API key generation, project naming conventions, date formatting

tests/unit_tests/test_saas_infra/
  test_railway_client.py  — GraphQL mocking + CLI subprocess mocking
  test_provisioner.py     — Full provisioning flow with mocked client
  test_models.py          — Data model behavior
  test_utils.py           — Validation, naming, date math
```

This module lives at `saas_infra/` (NOT inside `src/`). Tests need `sys.path.insert(0, repo_root)` via `Path(__file__).resolve().parents[3]` to import it.

## Critical Gotchas

### 1. Dual Auth Model

The `.env` file has a Railway **team token** (36-char UUID like `d3f0fcc0-a...`). The Railway CLI expects its own **session token** from `~/.railway/config.json` (308-char string starting with `rw_Fe26.2*...`).

If `RAILWAY_TOKEN` is in the subprocess environment, the CLI tries to use it and rejects the UUID format. `_run_cli()` strips `RAILWAY_TOKEN` from the subprocess env so the CLI falls back to its config file auth. The GraphQL client continues using the team token via Bearer header.

If you ever see "Unauthorized" from CLI commands, this is almost certainly the cause.

### 2. Service Propagation Delay

After `railway add -d postgres` completes, the service is NOT immediately visible via GraphQL. There's a ~15 second propagation delay. `_wait_for_service()` polls every 2 seconds for up to 30 seconds. All three service creation methods use this.

If you add new service creation methods, remember to use `_wait_for_service()` after the CLI call.

### 3. Domain URL Prefix

`railway domain --json` sometimes returns the full URL with `https://` prefix, sometimes just the bare domain. `generate_service_domain()` strips any protocol prefix. The provisioner then adds `https://` when constructing the gateway URL. Don't add the prefix anywhere else.

### 4. Service Names Are Case-Sensitive

Railway's template-provisioned databases get capitalized names: `Postgres`, `Redis`. The gateway service we create is lowercase: `gateway`. Variable references must match exactly: `${{Postgres.DATABASE_URL}}`, `${{Redis.REDIS_URL}}`. Getting the case wrong means the variable won't resolve at runtime.

### 5. CLI Context = Temp Directories

Every CLI operation that targets a specific project needs to run in a directory that's been `railway link`ed to that project. `_linked_project_dir()` creates a temp dir, links it, and yields the path. `create_project()` uses `railway init` which both creates and links in one step.

Don't try to share linked directories across projects or reuse them. Each operation gets its own ephemeral tmpdir.

### 6. projectUpdate GraphQL Mutation Format

The `projectUpdate` mutation takes `id` as a **separate top-level argument**, not inside `ProjectUpdateInput`:
```graphql
mutation($id: String!, $input: ProjectUpdateInput!) {
    projectUpdate(id: $id, input: $input) { id }
}
```

This is different from most Railway mutations where the ID is inside the input object.

## Provisioning Flow

`provisioner.py` `create_instance(name)` does this in sequence:

1. Validate name, check for duplicates
2. Generate proxy_api_key and admin_api_key
3. `railway init -n luthien-<name>` (creates project)
4. Find project via GraphQL to get project_id and environment_id
5. `railway add -d postgres` + wait for propagation
6. `railway add -d redis` + wait for propagation
7. `railway add --service gateway --repo LuthienResearch/luthien-proxy` + wait
8. `railway variables --service gateway --set "K=V" --set ... --skip-deploys` (all vars in one call)
9. `railway domain --service gateway --json` (generate public URL)

If any step fails after the project is created, the cleanup handler deletes the project.

Database templates (steps 5-6) auto-provision volumes and connection variables. No manual variable setup needed for databases.

## Soft Delete

Deletion uses a 7-day grace period by default. The project's `description` field is used as a tag: `deletion-scheduled:2026-02-12T18:00:00+00:00`. The `list` and `status` commands parse this tag to show deletion countdowns. `--force` bypasses the grace period and deletes immediately.

## Testing

```bash
# Unit tests (fast, no network)
uv run pytest tests/unit_tests/test_saas_infra/ -v

# E2E against live Railway (creates real resources, costs money)
set -a && source .env && set +a
uv run python -m saas_infra.cli create my-test --json
uv run python -m saas_infra.cli list --json
uv run python -m saas_infra.cli status my-test --json
uv run python -m saas_infra.cli delete my-test --force --yes --json
```

Unit tests mock both `subprocess.run` (for CLI) and `httpx.Client` (for GraphQL). The provisioner tests mock the entire `RailwayClient` interface.

## Future Work

Things that are known to be incomplete or could be improved:

- `redeploy` command uses GraphQL `deploymentTrigger` mutation — may need migration to CLI if it hits state transition issues
- Service status in `get_instance()` shows "unknown" for newly-created services (deployments haven't completed yet)
- No health check after provisioning — the gateway takes a few minutes to build and deploy
- No log streaming or deployment progress tracking
- The `whoami` command has a fallback for team tokens that can't query `me` — it lists projects instead
