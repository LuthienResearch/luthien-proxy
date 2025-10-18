# Luthien Observability Stack

Distributed tracing (Tempo) + Log aggregation (Loki) + Visualization (Grafana).

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

# Start observability only
docker compose --profile observability up -d tempo loki grafana

# Stop observability services
docker compose stop tempo loki grafana

# View logs
docker compose logs -f tempo loki grafana
```

## Access

- **Grafana UI:** http://localhost:3000 (auto-login, no password needed)
- **Tempo API:** http://localhost:3200 (backend only, used by Grafana)
- **Loki API:** http://localhost:3100 (backend only, used by Grafana)

## Application Integration

The Luthien application automatically sends telemetry when observability stack is running:

- **Traces** → Tempo (via OTLP at `tempo:4317`)
- **Logs** → Structured logs with trace correlation (viewable in Grafana)

Configuration is in `src/luthien_proxy/v2/telemetry.py`.

## Data Storage

All data is stored in `observability/data/` (gitignored):
- `data/tempo/` - Trace data (24h retention)
- `data/loki/` - Log data (24h retention)
- `data/grafana/` - Grafana settings & dashboards

**Retention:** 24 hours (automatic cleanup)
**Disk usage:** ~750MB steady state

## Architecture

```
Luthien App
    ↓ (traces via OTLP gRPC)
    ↓ port 4317
Tempo ─────┐
           │
    ↓ (structured logs)
    ↓ stdout
Loki ──────┤
           │
           ↓ (queries both)
        Grafana
           ↓
    http://localhost:3000
```

## Dashboards

Pre-configured dashboards (auto-loaded):
- **Luthien Overview** - Request timeline, latency, errors, recent traces

You can:
- Modify existing dashboards in the Grafana UI
- Create new dashboards (will be saved in `data/grafana/`)
- Export dashboards to `grafana/dashboards/*.json` for version control

## Troubleshooting

**Services won't start:**
```bash
# Check status
docker compose ps tempo loki grafana

# View logs
./scripts/observability.sh logs
```

**Can't connect to Grafana:**
- Check it's running: `docker compose ps grafana`
- Check port 3000 isn't in use: `lsof -i :3000`
- Try restarting: `./scripts/observability.sh restart`

**No traces/logs appearing:**
- Check app has `OTEL_ENABLED=true` (default)
- Verify Tempo is healthy: `docker compose ps tempo`
- Check app logs: `docker compose logs control-plane`

**Disk space issues:**
- Clean old data: `./scripts/observability.sh clean`
- Data auto-deletes after 24h

## Documentation

- **Full observability guide:** [../dev/context/observability-guide.md](../dev/context/observability-guide.md)
- **Query languages:**
  - TraceQL (searching traces): https://grafana.com/docs/tempo/latest/traceql/
  - LogQL (searching logs): https://grafana.com/docs/loki/latest/query/
