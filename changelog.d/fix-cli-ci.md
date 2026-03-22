---
category: Fixes
pr: 404
---

**Fix CLI install and CI publish pipeline**: CI workflows (`auto-tag-cli`, `release-cli`) were failing because `uv run pytest` didn't install dev extras. Install scripts now pull from GitHub source instead of stale PyPI package.
