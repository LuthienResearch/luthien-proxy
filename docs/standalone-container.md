# Standalone Container

A single Docker container that bundles the Luthien gateway, PostgreSQL, and Redis. Useful for quick deployments, demos, or environments where running `docker compose` is inconvenient.

> This is a **convenience option** alongside the existing `docker-compose.yaml` multi-container setup, which remains the recommended approach for development.

## Quick Start

```bash
# Build
docker build -f docker/Dockerfile.standalone -t luthien-standalone .

# Run (minimal — passthrough auth; clients use their own Anthropic credentials)
docker run -p 8000:8000 \
  -v luthien-pgdata:/var/lib/postgresql/data \
  -v luthien-redis:/data \
  -e ADMIN_API_KEY=admin-my-key \
  luthien-standalone
```

The gateway will be available at `http://localhost:8000`. Add `-e PROXY_API_KEY=...` and `-e ANTHROPIC_API_KEY=...` only if you want proxy-key auth with a shared server-side Anthropic credential (see the [README authentication section](../README.md#authentication) for the full picture).

## Environment Variables

All standard Luthien env vars work. The auth-related ones:

| Variable | Required | Default | Description |
|---|---|---|---|
| `PROXY_API_KEY` | No* | — | API key clients use to authenticate. Required when `AUTH_MODE=proxy_key`; optional for `both`/`passthrough`. |
| `ADMIN_API_KEY` | No* | — | API key for admin endpoints. Required when admin routes are reached from outside the container (localhost bypass covers loopback by default — see `LOCALHOST_AUTH_BYPASS`). |
| `ANTHROPIC_API_KEY` | No | — | Anthropic API key for upstream calls |
| `GATEWAY_PORT` | No | `8000` | Port the gateway listens on |
| `POLICY_CONFIG` | No | `/app/config/policy_config.yaml` | Policy config file path |
| `POSTGRES_USER` | No | `luthien` | PostgreSQL username |
| `POSTGRES_PASSWORD` | No | `luthien_dev_password` | PostgreSQL password |
| `POSTGRES_DB` | No | `luthien_control` | PostgreSQL database name |

`DATABASE_URL` and `REDIS_URL` are set automatically to point at the in-container services — you don't need to provide them.

> **Reverse proxy warning**: If you put a reverse proxy (Caddy, nginx, Traefik, Tailscale Funnel) in front of this container on the same host, every forwarded request appears as `127.0.0.1` to the gateway and bypasses admin auth via `LOCALHOST_AUTH_BYPASS=true` (the default). Set `LOCALHOST_AUTH_BYPASS=false` for that deployment pattern, or admin routes are effectively unauthenticated to external callers.

## Persistent Storage

Two volume mount points keep data across container restarts and rebuilds:

| Path | Contents |
|---|---|
| `/var/lib/postgresql/data` | PostgreSQL data directory |
| `/data` | Redis AOF + RDB snapshots |

Mount named volumes (recommended) or bind mounts to these paths:

```bash
# Named volumes (Docker manages storage location)
docker run \
  -v luthien-pgdata:/var/lib/postgresql/data \
  -v luthien-redis:/data \
  ...

# Bind mounts (you control the host path)
docker run \
  -v /path/on/host/pgdata:/var/lib/postgresql/data \
  -v /path/on/host/redis:/data \
  ...
```

## What Happens on Startup

1. PostgreSQL starts (initialises data directory on first run)
2. Database and user are created if they don't exist
3. Redis starts with AOF persistence enabled
4. Database migrations run (idempotent — skips already-applied ones)
5. The Luthien gateway starts on the configured port

## Production Considerations

The standalone container is designed for **convenience** — quick demos, local testing, and simple deployments. For production use, be aware of:

- **Set `POSTGRES_PASSWORD`**: The default password (`luthien_dev_password`) is for local development only. Always set a strong password via the `POSTGRES_PASSWORD` env var.
- **Root process**: The entry script runs as root. For production deployments with proper user isolation, use the multi-container `docker-compose.yaml` setup instead.
- **Single point of failure**: All services share one container. If the container goes down, everything goes down together.

## Using an `.env` File

You can pass an `.env` file instead of individual `-e` flags:

```bash
docker run -p 8000:8000 \
  -v luthien-pgdata:/var/lib/postgresql/data \
  -v luthien-redis:/data \
  --env-file .env \
  luthien-standalone
```

## Disabling OpenTelemetry

The standalone container doesn't include a Tempo instance, so OTel trace exports will log harmless warnings. To silence them:

```bash
docker run ... -e OTEL_ENABLED=false luthien-standalone
```
