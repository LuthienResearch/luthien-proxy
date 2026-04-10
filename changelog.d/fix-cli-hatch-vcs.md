---
category: Fixes
---

**luthien-cli build failure on recent checkouts**: Filter `git describe` to only consider `cli-v*` tags when resolving the CLI version. Previously, after the proxy's `v3.0.0` tag landed, any fresh checkout or worktree would fail to build with `UserWarning: tag 'v3.0.0' no version found` because hatch-vcs picked the nearest tag (`v3.0.0`) before the cli-specific `tag_regex` could filter it.
