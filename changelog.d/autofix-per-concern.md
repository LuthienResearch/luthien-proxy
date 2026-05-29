---
category: Features
---

**Automated maintenance autofix now opens one single-concern PR per failing check, with per-concern dedup** (`scripts/automated_maintenance/lib/autofix.sh`)
  - Each failing check (concern) gets its own focused fix session and its own draft PR on `maint-fix/<concern>/<run_id>`, instead of one PR bundling every failure.
  - Before attempting a concern, checks for an already-open autofix PR for that concern and skips it (no duplicate while a fix is in review); a novel concern still gets its own PR. Fails closed (skips) if the GitHub query errors.
  - `results.json` `autofix` is now keyed by concern; the dashboard renders a pill/PR-link per concern and stays backward-compatible with the legacy single-object shape.
