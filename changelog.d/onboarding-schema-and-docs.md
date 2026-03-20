---
category: Bug Fixes
pr: 399
---

**Fix onboarding crashes**: Bundle `sqlite_schema.sql` with the Python package so the gateway can create database tables in pip-installed environments. Remove `pyyaml` dependency from CLI by writing config YAML directly.
  - Gateway no longer crashes with "no such table: current_policy" on fresh `luthien onboard`
  - `_write_policy` no longer fails with `ModuleNotFoundError: No module named 'yaml'`
  - README now explains dashboard API key requirements and localhost auth bypass
