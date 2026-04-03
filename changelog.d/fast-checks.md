---
category: Features
pr: 489
---

**Fast dev checks**: Add `scripts/fast_checks.sh` for faster development iteration (~10s vs ~40s). Runs pyright and pytest in parallel with xdist, defaults to testing only changed files.
