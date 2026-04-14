---
category: Chores & Docs
---

**dev_checks: per-step timing instrumentation**: Added `--timing` / `--timing=PATH` flag to `scripts/dev_checks.sh` that writes one JSON line per step to `.dev_checks_timings.jsonl` (run_id, step, duration_s, exit_code, ts) and prints a sorted summary at the end. Added a `Testing & QA` section to `dev-README.md` documenting test tiers, dev_checks flags, and performance characteristics.
