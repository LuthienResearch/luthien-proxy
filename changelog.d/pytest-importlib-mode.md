---
category: Refactors
---

**Switch pytest to --import-mode=importlib**: Eliminate test filename collision workarounds and sys.path hacks by using fully-qualified module paths for test imports. Shared constants moved to `tests/constants.py`.
