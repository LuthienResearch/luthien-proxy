---
category: Fixes
pr: 454
---

**Docker onboard error messaging**: Narrow GHCR auth-failure detection from bare "denied" to "access denied" to avoid false positives on Docker socket permission errors, and strengthen test coverage for `_download_files` error paths.
