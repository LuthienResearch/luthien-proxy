# Luthien Observability Stack

Distributed tracing via Tempo with OpenTelemetry instrumentation.

## Quick Start

### Using Helper Script (Recommended)

```bash
# Start observability stack
./scripts/observability.sh up -d

# View logs
./scripts/observability.sh logs -f

# Check status
./scripts/observability.sh status

# Stop stack
./scripts/observability.sh down

# Remove all data
./scripts/observability.sh clean
```

### Using Docker Compose Directly

```bash
# Start with main app
docker compose --profile observability up -d

# Stop
docker compose --profile observability down
```

## Access

- **Tempo HTTP API:** http://localhost:3200
- **OTLP gRPC endpoint:** localhost:4317 (application sends traces here)

## Application Integration

The Luthien gateway automatically sends traces to Tempo via OTLP when the observability stack is running.

Configuration is in `src/luthien_proxy/telemetry.py`.

## Querying Traces

Search traces via the Tempo HTTP API:

```bash
# Search recent traces
curl http://localhost:3200/api/search

# Search by TraceQL query
curl 'http://localhost:3200/api/search?q=%7B%20span.%22luthien.call_id%22%20%3D%20%22your-call-id%22%20%7D'
```

## Data Storage

Trace data is stored in `observability/data/tempo/` (gitignored).

**Retention:** 24 hours (automatic cleanup)

## Troubleshooting

**Tempo won't start:**
```bash
docker compose ps tempo
./scripts/observability.sh logs
```

**No traces appearing:**
- Check app has `OTEL_ENABLED=true` (default)
- Verify Tempo is healthy: `docker compose ps tempo`
- Check gateway can reach Tempo: `docker compose exec gateway curl -v http://tempo:4317`

**Disk space issues:**
- Clean old data: `./scripts/observability.sh clean`
- Data auto-deletes after 24h
