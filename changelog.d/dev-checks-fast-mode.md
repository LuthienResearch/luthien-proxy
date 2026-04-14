---
category: Chores & Docs
---

**dev_checks: `--skip-reports` / `--fast` inner-loop mode**: Added `--skip-reports` flag that skips report-only steps (ruff docstrings, radon) and pytest coverage. Gating checks (ruff, pyright, pytest) still run. `--fast` is an alias that may enable more shortcuts in the future. Saves ~13s on a typical warm run (~45s → ~32s). Use while iterating; run the full gate before pushing.
