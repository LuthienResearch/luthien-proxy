---
category: Fixes
---

**Docker images publish on version tags again**: the build job skipped any push whose commit message contained `[skip auto-tag-proxy]`, which also matched release-tag pushes (the tag points at that changelog commit), so versioned images were never built. Tag pushes now always build.
