---
category: Fixes
pr: 455
---

**Docker local build fallback**: When `docker compose pull` fails (e.g. GHCR 403), onboarding now offers to clone the repo and build images locally instead of exiting.
