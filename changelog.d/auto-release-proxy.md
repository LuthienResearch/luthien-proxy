---
category: Features
---

**Auto-release for luthien-proxy**: on merge to main, automatically compile changelog fragments, cut a versioned section in CHANGELOG.md, tag the release, and trigger GitHub Release + Docker image publishing. Starts at v3.0.0, auto-increments patch. Also fixes Docker images reporting `0.0.0+sha` instead of the actual version when built from a tag.
