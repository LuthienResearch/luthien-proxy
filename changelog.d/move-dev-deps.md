---
category: Chores & Docs
pr: 444
---

**Move dev tools to dev dependencies**: Moved `pyright`, `vulture`, and `pytest-timeout` from production `[project.dependencies]` to the `[dependency-groups] dev` group so they aren't pulled in by `pip install luthien-proxy`.
