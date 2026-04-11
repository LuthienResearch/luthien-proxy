---
category: Fixes
---

**CLI absolute paths**: `luthien up` no longer fails with "Policy config not found" when `~/.luthien/luthien-proxy/config/` is empty.
  - `configure_local_mode()` now hardcodes `POLICY_CONFIG` to the managed install path (`~/.luthien/luthien-proxy/config/policy_config.yaml`), removing the implicit cwd dependency
  - `ensure_gateway_venv()` and `_download_files()` now write a default NoOpPolicy `policy_config.yaml` when the config directory is empty; existing user-customized files are never overwritten
