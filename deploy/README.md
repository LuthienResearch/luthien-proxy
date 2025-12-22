# Luthien Proxy Demo Deployment

Deploy Luthien Proxy to Railway for a publicly-accessible demo.

## Quick Start

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template?template=https://github.com/luthienresearch/luthien-proxy)

## Manual Setup

1. Create a new project in [Railway](https://railway.app)
2. Add a **PostgreSQL** database service
3. Add a **Redis** service
4. Add a new service from GitHub (this repo)
5. Set environment variables (see below)
6. Generate a domain in Settings → Networking

### Required Environment Variables

All of these must be set for the deployment to work:

| Variable | Value | Description |
|----------|-------|-------------|
| `GATEWAY_PORT` | `${{PORT}}` | Railway's dynamic port assignment |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` | PostgreSQL connection URL |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` | Redis connection URL |
| `PROXY_API_KEY` | `openssl rand -hex 32` | API key for client authentication |
| `ADMIN_API_KEY` | `openssl rand -hex 32` | API key for admin endpoints |
| `POLICY_CONFIG` | `/app/config/policy_config.yaml` | Path to default policy config |

### Recommended Environment Variables

| Variable | Value | Description |
|----------|-------|-------------|
| `OTEL_ENABLED` | `false` | Disable OpenTelemetry (no Tempo on Railway) |

### Optional Environment Variables

At least one LLM provider API key is needed to proxy requests:

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | Your OpenAI API key (for GPT models) |
| `ANTHROPIC_API_KEY` | Your Anthropic API key (for Claude models) |

## Post-Deployment

### Verify Deployment

```bash
# Health check
curl https://your-app.railway.app/health

# Test API (replace with your PROXY_API_KEY)
curl https://your-app.railway.app/v1/chat/completions \
  -H "Authorization: Bearer your-proxy-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### Configure Claude Code

```bash
# Set the API base URL
export OPENAI_API_BASE=https://your-app.railway.app/v1
export OPENAI_API_KEY=your-proxy-api-key

# Or for Anthropic
export ANTHROPIC_API_BASE=https://your-app.railway.app
export ANTHROPIC_API_KEY=your-proxy-api-key
```

### Database Migrations

Migrations run automatically on startup when `DATABASE_URL` is set. No manual intervention required.

## Troubleshooting

### Health Check Fails / App Won't Start

Check the deployment logs (`railway logs`) for these common errors:

| Error | Cause | Fix |
|-------|-------|-----|
| `RuntimeError: No policy configured` | Missing `POLICY_CONFIG` | Set `POLICY_CONFIG=/app/config/policy_config.yaml` |
| Connection refused on port | Wrong port binding | Set `GATEWAY_PORT=${{PORT}}` |
| Database connection failed | Missing DATABASE_URL | Verify PostgreSQL service is linked |
| Redis connection failed | Missing REDIS_URL | Verify Redis service is linked |

### Logs Command

```bash
# View build logs
railway logs --build

# View runtime logs
railway logs
```

## Security Checklist

Before going public:

- [ ] Generate strong, unique `PROXY_API_KEY` (32+ characters)
- [ ] Generate strong, unique `ADMIN_API_KEY` (32+ characters)
- [ ] Review policy configuration for your use case
- [ ] Keep API keys secret - never commit to source control

## Cost

Railway offers a free tier. Production usage is approximately $5/month.

## Support

- [GitHub Issues](https://github.com/luthienresearch/luthien-proxy/issues)
- [Documentation](https://github.com/luthienresearch/luthien-proxy#readme)
