# SaaS Infrastructure CLI

Provision and manage luthien-proxy instances on Railway for multi-tenant SaaS deployment.

## Prerequisites

1. **Railway Account**: Sign up at [railway.app](https://railway.app)
2. **Railway Token**: Generate an account token at [railway.app/account/tokens](https://railway.app/account/tokens)
3. **GitHub Repo Access**: Railway needs access to deploy from the luthien-proxy repo

## Setup

Set the required environment variable:

```bash
export RAILWAY_TOKEN="your-railway-token"

# Optional: specify a team (otherwise uses personal account)
export RAILWAY_TEAM_ID="your-team-id"
```

## Usage

All commands are run from the repository root:

```bash
# Create a new instance
uv run python -m saas_infra.cli create my-tenant

# List all instances
uv run python -m saas_infra.cli list

# Check instance status
uv run python -m saas_infra.cli status my-tenant

# Redeploy an instance (pulls latest from main)
uv run python -m saas_infra.cli redeploy my-tenant

# Delete an instance (7-day grace period)
uv run python -m saas_infra.cli delete my-tenant

# Force delete immediately
uv run python -m saas_infra.cli delete my-tenant --force

# Cancel scheduled deletion
uv run python -m saas_infra.cli cancel-delete my-tenant

# Check Railway authentication
uv run python -m saas_infra.cli whoami
```

### JSON Output

All commands support `--json` for machine-readable output:

```bash
uv run python -m saas_infra.cli list --json | jq '.instances[].name'
```

## Commands

### create

Creates a new fully-isolated luthien-proxy instance with:
- Dedicated Railway project
- PostgreSQL database
- Redis instance
- Gateway service (deployed from main branch)
- Auto-generated API keys

```bash
uv run python -m saas_infra.cli create <name> [options]

Options:
  --repo       Custom GitHub repo in owner/repo format (default: LuthienResearch/luthien-proxy)
  --json       Output as JSON
```

**Output includes credentials** - save them, they're only shown once!

### list

Lists all luthien-proxy instances with their status.

```bash
uv run python -m saas_infra.cli list [--json]
```

### status

Shows detailed information about a specific instance.

```bash
uv run python -m saas_infra.cli status <name> [--json]
```

### delete

Schedules an instance for deletion with a 7-day grace period.

```bash
uv run python -m saas_infra.cli delete <name> [options]

Options:
  --force     Delete immediately without grace period
  --yes, -y   Skip confirmation prompt
  --json      Output as JSON
```

### cancel-delete

Cancels a scheduled deletion.

```bash
uv run python -m saas_infra.cli cancel-delete <name> [--json]
```

### redeploy

Triggers a new deployment from the latest main branch.

```bash
uv run python -m saas_infra.cli redeploy <name> [--json]
```

### whoami

Shows current Railway user info and teams.

```bash
uv run python -m saas_infra.cli whoami [--json]
```

## Demo Web UI

A standalone web UI is available for trying out instance management in the browser:

```bash
# Load Railway credentials
set -a && source .env && set +a

# Start the demo server on port 8899
uv run python -m saas_infra.demo
```

Then open [http://localhost:8899](http://localhost:8899). The UI lets you create, inspect, and delete instances. Each instance row links to both the public gateway endpoint and the Railway console.

This is a demo tool, not a production service — no auth, no persistence, no error recovery.

## Instance Naming

Instance names must be:
- Lowercase alphanumeric with hyphens
- Start with a letter
- Not end with a hyphen
- Maximum 63 characters

Examples: `acme-corp`, `test-instance-1`, `mycompany`

## Architecture

Each instance is a separate Railway project containing:

```
luthien-<instance-name>/
├── Postgres (service)
├── Redis (service)
└── gateway (service)
    └── Deployed from GitHub
```

Railway's variable references connect the services:
- `DATABASE_URL` → Postgres connection string
- `REDIS_URL` → Redis connection string

## Soft Delete

When you run `delete` without `--force`:

1. Instance is marked with a deletion timestamp
2. Instance remains running for 7 days
3. After 7 days, a cleanup process can permanently delete it

To implement automated cleanup, run a scheduled job that:
1. Lists instances with `list --json`
2. Checks `deletion_scheduled_at` timestamps
3. Runs `delete --force` for expired instances

## Token Types

Railway supports different token types:

- **Personal Account Token**: Can query user info (`whoami`), create projects, access all personal resources
- **Team Token**: Cannot query user info, but can manage all team projects

The CLI automatically detects which token type you're using and adjusts behavior accordingly.

## Troubleshooting

### "RAILWAY_TOKEN environment variable is required"

Set your Railway token:
```bash
export RAILWAY_TOKEN="your-token-here"
```

### "Instance already exists"

Choose a different name or delete the existing instance first.

### "Endpoint too recently updated - please wait a few minutes"

Railway has rate limits on project and service creation. Wait a few minutes between operations, especially after:
- Creating or deleting projects
- Creating multiple services
- Rapid successive API calls

This is a Railway-side rate limit, not a bug in the CLI.

### Deployment stuck

Check Railway dashboard for deployment logs. The gateway may need:
- GitHub repo access configured in Railway
- Proper Dockerfile at `docker/Dockerfile.gateway`

### Health check failing

Ensure the gateway's `/health` endpoint is responding. Check:
- Database connectivity (`DATABASE_URL`)
- Redis connectivity (`REDIS_URL`)
- Port binding (`GATEWAY_PORT`)
