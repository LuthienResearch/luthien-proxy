---
category: Fixes
---

**Automated maintenance: fix stray-brace bug that silently dropped every check result** (`scripts/automated_maintenance/lib/config.sh`)
  - `maint_record_check`'s `extra="${4:-{}}"` default parsed as `${4:-{}` plus a literal `}`, appending a stray brace to every payload. `json.loads(extra)` then failed with "Extra data", so no check was ever recorded — every run produced `checks: {}` and `overall: unknown`, leaving the dashboard blank.
  - Added a subprocess-driven regression test (`test_record_check.py`) that exercises the real bash function.
