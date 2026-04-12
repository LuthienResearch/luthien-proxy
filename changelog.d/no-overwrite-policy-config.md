---
category: Fixes
---

**`luthien onboard` no longer overwrites a customized `policy_config.yaml`**: `_write_policy()` now skips the write if the file already exists, preserving any user-configured policy.
