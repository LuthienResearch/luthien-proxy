---
category: Chores & Docs
---

**dev_checks: parallel pytest workers (xdist, default 4)**: `scripts/dev_checks.sh` now runs pytest with `-n 4` by default via `pytest-xdist`. Saves ~12s on the full gate (coverage instrumentation is CPU-bound and parallelizes well) and ~4s in `--skip-reports` mode. Override via `--workers=N` flag or `DEV_CHECKS_PYTEST_WORKERS` env var; use `--workers=1` to disable for debugging flakes or interleaved output.
