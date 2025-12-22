# Luthien Proxy Demo Deployment

This directory contains configurations for deploying Luthien Proxy to various cloud platforms and self-hosted environments.

## Quick Start

Choose your preferred deployment option:

| Platform | Difficulty | Cost | Best For |
|----------|------------|------|----------|
| [Fly.io](#flyio) | Easy | Free tier available | Production demos |
| [Render](#render) | Very Easy | Free tier (limited) | Quick testing |
| [Railway](#railway) | Very Easy | Free tier available | Rapid prototyping |
| [VPS/Docker](#vpsdocker) | Medium | VPS cost (~$5-20/mo) | Full control |

## Prerequisites

All deployments require:
- At least one LLM provider API key (OpenAI or Anthropic)
- A secure API key for proxy authentication (will be generated)

## Fly.io

Fly.io offers a generous free tier with managed PostgreSQL and Redis.

### One-Click Deploy

```bash
# Clone the repository
git clone https://github.com/luthienresearch/luthien-proxy.git
cd luthien-proxy

# Run the deployment script
./deploy/fly/deploy.sh my-luthien-demo
```

### Manual Deploy

```bash
# Install flyctl
curl -L https://fly.io/install.sh | sh

# Login
fly auth login

# Create app
fly apps create my-luthien-demo

# Create PostgreSQL (free tier)
fly postgres create --name my-luthien-db
fly postgres attach my-luthien-db --app my-luthien-demo

# Create Redis (Upstash, free tier)
fly redis create --name my-luthien-redis

# Set secrets
fly secrets set \
  PROXY_API_KEY="$(openssl rand -hex 32)" \
  ADMIN_API_KEY="$(openssl rand -hex 32)" \
  OPENAI_API_KEY="sk-your-key" \
  --app my-luthien-demo

# Deploy
fly deploy --config deploy/fly/fly.toml --app my-luthien-demo
```

**Estimated Cost:** Free for hobby use, ~$5-10/month for production

## Render

Render provides one-click deployment from GitHub with a Blueprint.

### One-Click Deploy

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/luthienresearch/luthien-proxy)

### Manual Setup

1. Fork the repository on GitHub
2. Go to [Render Dashboard](https://dashboard.render.com)
3. Click "New Blueprint Instance"
4. Connect your forked repository
5. Configure environment variables in the dashboard:
   - `PROXY_API_KEY`: Generate with `openssl rand -hex 32`
   - `ADMIN_API_KEY`: Generate with `openssl rand -hex 32`
   - `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`

**Note:** Render's free PostgreSQL sleeps after inactivity. For production, upgrade to a paid plan.

**Estimated Cost:** Free tier available, ~$7/month for always-on

## Railway

Railway offers simple deployment with automatic PostgreSQL/Redis provisioning.

### One-Click Deploy

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template?template=https://github.com/luthienresearch/luthien-proxy)

### Manual Setup

1. Create a new project in [Railway](https://railway.app)
2. Add a PostgreSQL database service
3. Add a Redis service
4. Add a new service from GitHub (this repo)
5. Set environment variables:
   - `GATEWAY_PORT`: `${{PORT}}`
   - `DATABASE_URL`: `${{Postgres.DATABASE_URL}}`
   - `REDIS_URL`: `${{Redis.REDIS_URL}}`
   - `PROXY_API_KEY`: Your generated key
   - `ADMIN_API_KEY`: Your generated admin key
   - `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`

**Estimated Cost:** Free tier available, ~$5/month for production

## VPS/Docker

Deploy to any VPS (DigitalOcean, Linode, Hetzner, etc.) with Docker.

### Quick Deploy

```bash
# SSH to your VPS
ssh root@your-server.com

# Clone repository
git clone https://github.com/luthienresearch/luthien-proxy.git
cd luthien-proxy

# Run deployment script (handles Docker installation, etc.)
./deploy/docker/deploy-vps.sh your-domain.com
```

### Manual Setup

```bash
# Copy environment template
cp deploy/docker/.env.prod.example deploy/docker/.env

# Edit configuration
nano deploy/docker/.env
# Set: DOMAIN_NAME, POSTGRES_PASSWORD, PROXY_API_KEY, ADMIN_API_KEY, LLM keys

# Deploy
cd deploy/docker
docker compose -f docker-compose.prod.yaml up -d
```

### What's Included

- **Caddy**: Automatic HTTPS with Let's Encrypt
- **PostgreSQL**: Persistent database
- **Redis**: Caching and ephemeral state
- **Gateway**: The Luthien Proxy service

**Requirements:**
- VPS with 1GB+ RAM
- Domain name pointing to your server
- Ports 80 and 443 open

**Estimated Cost:** $5-20/month depending on VPS provider

## Post-Deployment

### Verify Deployment

```bash
# Health check
curl https://your-domain.com/health

# Test API (replace with your PROXY_API_KEY)
curl https://your-domain.com/v1/chat/completions \
  -H "Authorization: Bearer your-proxy-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### Configure Claude Code

To use your deployed proxy with Claude Code:

```bash
# Set the API base URL
export OPENAI_API_BASE=https://your-domain.com/v1
export OPENAI_API_KEY=your-proxy-api-key

# Or for Anthropic
export ANTHROPIC_API_BASE=https://your-domain.com
export ANTHROPIC_API_KEY=your-proxy-api-key
```

### Run Migrations

For manual deployments, run database migrations:

```bash
# Fly.io
fly ssh console -C "for f in /app/migrations/*.sql; do psql \$DATABASE_URL -f \$f; done"

# Docker
docker compose exec gateway sh -c 'for f in /app/migrations/*.sql; do psql $DATABASE_URL -f $f; done'
```

## Security Checklist

Before going public:

- [ ] Generate strong, unique `PROXY_API_KEY` (32+ characters)
- [ ] Generate strong, unique `ADMIN_API_KEY` (32+ characters)
- [ ] Generate strong database password
- [ ] Configure a real domain with HTTPS
- [ ] Limit access to admin endpoints if needed
- [ ] Review policy configuration for your use case
- [ ] Set up monitoring/alerts (optional)

## Troubleshooting

### Service won't start

Check logs:
```bash
# Docker
docker compose logs gateway

# Fly.io
fly logs

# Railway/Render
Check dashboard logs
```

### Database connection issues

Ensure migrations have run:
```bash
# Check if tables exist
psql $DATABASE_URL -c "\dt"
```

### HTTPS not working

- Verify domain DNS points to your server
- Check Caddy logs: `docker compose logs caddy`
- Ensure ports 80/443 are open

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Client    │────▶│   Caddy     │────▶│   Gateway   │
│(Claude Code)│     │   (HTTPS)   │     │  (FastAPI)  │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                               │
                          ┌────────────────────┼────────────────────┐
                          ▼                    ▼                    ▼
                    ┌──────────┐         ┌──────────┐         ┌──────────┐
                    │PostgreSQL│         │  Redis   │         │ LLM API  │
                    │(policies)│         │ (cache)  │         │(upstream)│
                    └──────────┘         └──────────┘         └──────────┘
```

## Support

- [GitHub Issues](https://github.com/luthienresearch/luthien-proxy/issues)
- [Documentation](https://github.com/luthienresearch/luthien-proxy#readme)
