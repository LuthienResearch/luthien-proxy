---
category: Chores & Docs
---

**Claude review skips fork PRs instead of failing**: fork pull requests get no repository secrets, so the review job always failed on them. It now skips; maintainers review fork PRs by hand.
