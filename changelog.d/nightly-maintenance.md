---
category: Features
---

**Nightly maintenance pipeline (`scripts/nightly/`)**: portable scheduled
maintenance for luthien-proxy that runs the check suite, sweeps for doc
drift, optionally autofixes failures, and publishes a static dashboard.

  - Single shell entry point with launchd (macOS) and systemd-user (Linux)
    deploy templates.
  - Headless `claude` integration for doc-drift detection and autonomous
    fix attempts; autofix is opt-in and capped by `AUTOFIX_MAX_BUDGET_USD`.
  - Static HTML dashboard generated per run; point any web server at
    `$NIGHTLY_PUBLIC_DIR`.
