---
category: Chores & Docs
---

**Security policy and Dependabot**: `SECURITY.md` explains how to report a vulnerability privately, and `.github/dependabot.yml` opens weekly dependency updates for Python (uv), the telemetry worker (npm), Docker base images and GitHub Actions, with minor and patch updates grouped into one pull request per ecosystem
  - Postgres major versions and Python minor or major versions of the Docker base images are left out, since they need a data or runtime migration
