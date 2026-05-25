# Performance Testing Guidelines

> Canonical file — `CLAUDE.md` in this directory is a symlink to this file. Edit `AGENTS.md` only.

## Purpose

Performance tests measure gateway latency and throughput under realistic conditions. They validate that the gateway meets SLO targets for page load, transcript rendering, and API payload sizes. Tests are opt-in and excluded from default pytest runs to avoid slowing down CI.

## Marker

Performance tests use the `@pytest.mark.perf` marker:

```python
@pytest.mark.perf
async def test_page_load_latency(perf_fixture):
    # Test code
    pass
```

The `perf` marker is **excluded by default** from `pytest` runs. To run perf tests:

```bash
./scripts/run_perf.sh
# or directly:
uv run pytest -m perf tests/luthien_proxy/perf_tests/ -v
```

## Running

### Default pytest (excludes perf)

```bash
# Unit tests only (perf excluded)
uv run pytest tests/luthien_proxy/unit_tests

# All tiers except perf
./scripts/dev_checks.sh
```

### Perf tests only

```bash
# Run all perf tests
./scripts/run_perf.sh

# Run specific perf test
./scripts/run_perf.sh -- -k "test_page_load"

# Run with verbose output
./scripts/run_perf.sh -- -vv
```

### Test Infrastructure

Perf tests use Playwright for browser automation and timing measurement. Fixtures are defined in `conftest.py`:

- **Browser fixtures**: `browser`, `page` — Chromium browser instance and page context
- **Gateway fixtures**: `perf_gateway_url`, `perf_admin_api_key` — isolated perf test gateway
- **Timing fixtures**: `measure_time()` — context manager for latency measurement
- **Database fixtures**: `perf_db_path` — isolated SQLite database for perf tests (never touches dev DB)

## SLO Definitions

Performance targets are measured on a local network with sami-like fixture data (78 sessions, largest ~442 messages).

### Page Load SLO

**Metric**: time-to-first-turn-painted (DOM mutation observer on messages container)

- **Local network**: < 2 seconds
- **Throttled (Tailscale Funnel ~1 Mbps + 300ms RTT)**: < 5 seconds

### Transcript Open SLO

**Metric**: time-to-first-turn-painted after clicking a session in history list

- **Local network**: < 1 second
- **Throttled**: < 5 seconds

### Scroll Performance SLO

**Metric**: frame rate during transcript scroll (p95 frame time)

- **Local network**: < 33ms per frame (p95)
- **Throttled**: < 100ms per frame (p95)

### Payload Size SLO

**Metric**: gzipped response size for first page of results

- `/api/history/sessions` (first page): < 50 KB gzipped
- `/api/history/sessions/{id}` (first page): < 100 KB gzipped

### Measurement Methodology

- **Same machine for before and after**: Ensure consistent hardware
- **Median + p95 over ≥5 runs**: Report both metrics
- **Cold cache first run reported separately**: Distinguish cold-start from warm-cache behavior
- **Chromium only**: Firefox and WebKit do not support CDP bandwidth shaping for throttled tests
