---
category: Chores & Docs
---

**dev_checks: `--fast` inner-loop mode**: Added `--fast` flag that skips coverage in pytest and skips report-only steps (ruff docstrings, radon). Gating checks (ruff, pyright, pytest) still run. Saves ~13s on a typical warm run (~45s → ~32s). Use while iterating; run the full gate before pushing.
