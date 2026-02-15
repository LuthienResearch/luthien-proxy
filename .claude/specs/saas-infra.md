# SaaS Infrastructure Provisioning

## Overview

Build CLI tooling to provision and manage independent luthien-proxy instances on Railway for multi-tenant SaaS deployment. Each tenant gets a fully isolated environment (dedicated Railway project with gateway, Postgres, and Redis) accessible at a unique internet endpoint. The tooling uses Railway's GraphQL API to automate provisioning while keeping Railway itself as the source of truth for instance state.

## Goals

- Provision complete luthien-proxy stacks (gateway + Postgres + Redis) on Railway with a single command
- Enable operators to manage instance lifecycle: create, list, status, delete, redeploy
- Support future self-service by tenants via web dashboard (not in scope for MVP)
- Maintain full tenant isolation at the infrastructure level
- Provide both human-friendly and JSON output for scripting

## Non-Goals

- Web dashboard or customer-facing UI (future work)
- Billing integration (future work)
- Custom domain support (future work - Railway auto-generated domains for now)
- Centralized observability beyond Railway's built-in logging (future work)
- Shared database/Redis across tenants (explicit anti-goal for isolation)
- Auto-deploy when main branch updates (explicit anti-goal - updates are explicit)

## Requirements

### Core Provisioning

1. **Full Stack Creation**: Each `create` command provisions:
   - New Railway project named `luthien-<instance-name>`
   - PostgreSQL database service
   - Redis service
   - Gateway service deploying from main branch
   - Environment variables configured with service references

2. **Instance Isolation**:
   - Separate Railway project per tenant
   - Dedicated Postgres and Redis per tenant
   - Per-instance configuration (policy config, API keys, model allowlists, rate limits)
   - Per-instance upstream LLM credentials (tenants bring their own keys)

3. **Instance Lifecycle**:
   - Long-lived instances (persist until explicitly deleted)
   - Soft delete with 7-day grace period before actual deletion
   - Deletion state tracked via Railway project tags/description

### CLI Commands

| Command | Description |
|---------|-------------|
| `create <name>` | Provision new instance with generated secure keys and default config |
| `list` | Show all luthien-* projects with status |
| `status <name>` | Detailed status of specific instance (services, URLs, health) |
| `delete <name>` | Mark instance for deletion (7-day grace period) |
| `redeploy <name>` | Trigger redeployment of gateway service from latest main |

### Configuration

- **Authentication**: `RAILWAY_TOKEN` environment variable (Railway account token)
- **Instance Names**: DNS-safe (lowercase alphanumeric + hyphens, max 63 chars)
- **Default Config**: Secure API keys auto-generated, default policy config applied
- **Per-Instance Config**: Policy YAML, PROXY_API_KEY, upstream LLM keys, model allowlists, rate limits (modifiable post-creation)

### Error Handling

- Name collision: Fail with clear error message
- Partial provisioning failure: Best-effort cleanup of created resources, log what was created for manual review
- Invalid instance name: Reject with validation error before API calls

## Technical Approach

### Directory Structure

```
saas_infra/              # Note: underscore for valid Python package name
├── __init__.py
├── cli.py              # Click-based CLI entry point
├── railway_client.py   # GraphQL API wrapper
├── provisioner.py      # Orchestrates stack creation
├── models.py           # Data classes for instances, configs
├── utils.py            # Name validation, key generation
└── README.md           # Usage documentation
```

### Railway GraphQL API Integration

Use `httpx` for HTTP requests with manual GraphQL query construction:

```python
# Core mutations needed:
- projectCreate(name, teamId)
- serviceCreate(projectId, source)  # For Postgres, Redis, Gateway
- variableUpsert(projectId, environmentId, serviceId, name, value)
- projectDelete(projectId)
- deploymentTrigger(serviceId, environmentId)

# Core queries needed:
- projects(teamId) - list all projects
- project(id) - get project details including services, domains
```

### Provisioning Flow

1. Validate instance name (DNS-safe, not already exists)
2. Create Railway project `luthien-<name>`
3. Create Postgres service
4. Create Redis service
5. Create Gateway service (from GitHub repo, main branch)
6. Configure environment variables with service references:
   - `DATABASE_URL=${{Postgres.DATABASE_URL}}`
   - `REDIS_URL=${{Redis.REDIS_URL}}`
   - `PROXY_API_KEY=<generated>`
   - `ADMIN_API_KEY=<generated>`
   - `GATEWAY_PORT=${{PORT}}`
7. Wait for initial deployment to complete
8. Output instance URL and credentials

### Soft Delete Implementation

- `delete` command adds tag `deletion-scheduled: <ISO-timestamp-7-days-out>` to project
- `list` command shows deletion-scheduled instances with countdown
- Separate cleanup script/cron that:
  - Queries projects with `deletion-scheduled` tag
  - Deletes projects where scheduled time has passed
  - Can be run manually or via scheduled task

### Output Formats

Human-friendly (default):
```
Instance: my-tenant
Status: running
URL: https://luthien-my-tenant-abc123.railway.app
Services:
  - gateway: deployed (healthy)
  - postgres: running
  - redis: running
Created: 2024-01-15 10:30:00 UTC
```

JSON (`--json` flag):
```json
{
  "name": "my-tenant",
  "status": "running",
  "url": "https://luthien-my-tenant-abc123.railway.app",
  "services": {...},
  "created_at": "2024-01-15T10:30:00Z"
}
```

## Open Questions

1. **Team ID**: Does the Railway account have/need a team, or deploy to personal account? Need to determine at implementation time.

2. **GitHub Connection**: How should the gateway service connect to the GitHub repo? Railway may need repo authorization - verify during implementation.

3. **Cleanup Automation**: Should the soft-delete cleanup be a cron job, a Railway scheduled service, or manual operator responsibility?

4. **Config Updates**: Post-creation config changes (updating env vars) - add as separate command or defer to Railway dashboard?

## Acceptance Criteria

1. **Create Command Works**
   - [ ] `uv run python -m saas_infra.cli create test-instance` provisions complete stack
   - [ ] Instance accessible at Railway-generated URL
   - [ ] Postgres and Redis properly connected
   - [ ] Health endpoint returns 200

2. **List Command Works**
   - [x] Shows all luthien-* projects
   - [x] Displays status, URL, creation time
   - [x] Shows deletion-scheduled instances with countdown
   - [x] `--json` flag outputs valid JSON

3. **Status Command Works**
   - [x] Shows detailed instance information
   - [x] Includes service health status
   - [ ] Shows configured environment variables (redacted secrets) - not yet implemented

4. **Delete Command Works**
   - [ ] Marks instance for deletion with 7-day grace period
   - [ ] Instance still accessible during grace period
   - [ ] Clear confirmation prompt before marking

5. **Redeploy Command Works**
   - [ ] Triggers new deployment of gateway service
   - [ ] Deploys latest main branch code
   - [ ] Shows deployment progress/status

6. **Error Handling**
   - [ ] Duplicate name rejected with clear error
   - [ ] Invalid names rejected before API calls
   - [ ] Partial failures logged with cleanup attempted

7. **Documentation**
   - [ ] README in saas-infra/ with usage examples
   - [ ] Required env vars documented
