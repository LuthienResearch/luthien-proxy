# Luthien Proxy Demo Deployment

Deploy Luthien Proxy to Railway for a publicly-accessible demo.

## Quick Start

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template?template=https://github.com/luthienresearch/luthien-proxy)

## Manual Setup

1. Create a new project in [Railway](https://railway.app)
2. Add a **PostgreSQL** database service
3. Add a **Redis** service
4. Add a new service from GitHub (this repo)
5. Set environment variables:

| Variable | Value |
|----------|-------|
| `GATEWAY_PORT` | `${{PORT}}` |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` |
| `PROXY_API_KEY` | Generate with `openssl rand -hex 32` |
| `ADMIN_API_KEY` | Generate with `openssl rand -hex 32` |
| `OPENAI_API_KEY` | Your OpenAI key (if using OpenAI models) |
| `ANTHROPIC_API_KEY` | Your Anthropic key (if using Claude models) |

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

**Important:** Keep your generated API keys secret. Do not share them or commit them to source control.

## Security Checklist

Before going public:

- [ ] Generate strong, unique `PROXY_API_KEY` (32+ characters)
- [ ] Generate strong, unique `ADMIN_API_KEY` (32+ characters)
- [ ] Review policy configuration for your use case

## Cost

Railway offers a free tier. Production usage is approximately $5/month.

## Support

- [GitHub Issues](https://github.com/luthienresearch/luthien-proxy/issues)
- [Documentation](https://github.com/luthienresearch/luthien-proxy#readme)
