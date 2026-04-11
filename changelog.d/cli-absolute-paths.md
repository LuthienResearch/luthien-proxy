---
category: Fixes
---

**CLI absolute paths**: `luthien up` no longer fails with "Policy config not found" when `~/.luthien/luthien-proxy/config/` is empty.
  - `POLICY_CONFIG` now defaults to an absolute path (via `os.path.abspath()`) in both `configure_local_mode()` and `auto_provision_defaults()`, so the gateway works regardless of working directory
  - `ensure_gateway_venv()` and `_download_files()` now write a default NoOpPolicy `policy_config.yaml` when the config directory is empty; existing user-customized files are never overwritten
