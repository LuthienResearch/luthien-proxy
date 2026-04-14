# Deploy Luthien Proxy to Railway

One-click deploy gives you a running proxy endpoint. No API keys or database
setup required — just deploy, generate a domain, and point Claude Code at it.

## Quick Start (3 steps)

### 1. Deploy

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template?template=https://github.com/LuthienResearch/luthien-proxy)

### 2. Generate a domain

Once deployed, go to your service in the Railway dashboard:

**Settings → Networking → Generate Domain**

Copy the generated URL (e.g. `https://luthien-proxy-production-xxxx.up.railway.app`).

### 3. Connect Claude Code

```bash
export ANTHROPIC_BASE_URL=https://your-domain.up.railway.app
```

That's it. Claude Code uses your existing Claude subscription (OAuth pass-through) —
no extra API key needed. All requests flow through the proxy, where the default
policies log conversations and apply safety rules via a judge LLM.

> **Note:** The safety policy makes an additional LLM call (Haiku) per response
> to review content. This counts against your Claude usage. To disable it, set
> `POLICY_CONFIG` to a config without the SimpleLLMPolicy, or remove it from
> `config/railway_policy_config.yaml`.

## What you get out of the box

| Feature | Default |
|---------|---------|
| Auth mode | **Passthrough** — your Claude session flows through, no server API key needed. The endpoint is public: anyone with the URL can proxy through it using their own credentials. Set `CLIENT_API_KEY` to restrict access to a single shared value. |
| Database | **SQLite** — zero config, stored on Railway's ephemeral disk (lost on redeploy; add Postgres for durability) |
| Policies | **Debug logging** (activity monitor at `/activity`) + **English safety rules** (redact secrets, block harmful content, professional tone) |
| Admin API | Auto-generated key (printed in deploy logs) |

## Set a budget limit (recommended)

Railway charges per-resource usage. Set a spending limit as a safety valve:

1. Go to **Railway dashboard → Settings → Usage**
2. Set a monthly limit (e.g. $5–20/month for typical single-user usage)

This prevents runaway costs if something goes wrong. Railway usage for the proxy
itself is minimal (the LLM API costs are on your Claude subscription, not Railway).

## Customizing policies

The default policy config is at `config/railway_policy_config.yaml`. It chains
two policies:

1. **Debug logging** — records all requests/responses, powers the `/activity` UI
2. **English safety rules** — a SimpleLLMPolicy that reviews responses using a
   judge LLM with plain-English instructions (best-effort: fails open on errors)

To customize the safety rules, edit the `instructions` field in the YAML. You
can write rules in plain English — the judge LLM interprets and applies them.

To use a different policy config entirely, set the `POLICY_CONFIG` environment
variable in your Railway service settings.

### Example: Custom rules

```yaml
instructions: |
  Review each response and apply these rules:
  1. Never include real email addresses — replace with example@example.com
  2. Code suggestions must include error handling
  3. Responses should be concise — no more than 3 paragraphs for explanations
```

## Viewing the activity monitor

Visit `https://your-domain.up.railway.app/activity` to see a live feed of
proxied conversations. The `/diffs` page shows before/after comparisons when
policies modify responses.

## Advanced configuration

Override any default by setting environment variables in your Railway service:

| Variable | Default on Railway | Description |
|----------|--------------------|-------------|
| `AUTH_MODE` | `passthrough` | `passthrough`, `client_key`, or `both` |
| `POLICY_CONFIG` | `config/railway_policy_config.yaml` | Path to policy YAML |
| `CLIENT_API_KEY` | *(not set)* | Shared value the gateway will accept as a client credential. Clients set it as their own `ANTHROPIC_API_KEY`. |
| `ANTHROPIC_API_KEY` | *(not set)* | Server-side Anthropic credential used to forward requests whose token matched `CLIENT_API_KEY`. Required alongside `CLIENT_API_KEY` for matched traffic to reach Anthropic, otherwise `/v1/messages` returns 500 for those requests. |
| `ADMIN_API_KEY` | *(auto-generated)* | Check deploy logs for the generated value |
| `DATABASE_URL` | *(SQLite)* | Add a Postgres service for durable storage |
| `REDIS_URL` | *(not set)* | Add Redis for real-time UI events |

### Upgrading to Postgres

For durable storage (survives redeploys), add a PostgreSQL service in Railway:

1. In your Railway project, click **+ New** → **Database** → **PostgreSQL**
2. Railway auto-populates `DATABASE_URL` — the gateway picks it up on next deploy
3. Migrations run automatically on startup

## Troubleshooting

### Health check fails on first deploy

The Docker image takes 2-3 minutes to build on first deploy. Railway's health
check has a 60-second timeout, which should be sufficient after the build completes.
If it keeps failing, check the deploy logs for errors.

### "Application not found"

You haven't generated a domain yet. Go to Settings → Networking → Generate Domain.

### Admin API key

The auto-generated admin key is printed in the deploy logs. Search for
`AUTO-CONFIGURED` in the Railway log viewer. You'll need this key to access
`/api/admin/*` endpoints and the policy configuration UI.

## Cost

Railway proxy resource usage is approximately **$5/month** for a single-user
deployment. LLM API costs are billed through your Claude subscription, not Railway.
