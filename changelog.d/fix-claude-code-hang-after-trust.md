---
category: Fixes
pr: 490
---

**Onboarding QA fixes**: Multiple fixes from first-round QA testing
  - Install script now checks for working `git` before proceeding (macOS Xcode CLI tools)
  - Fixed wrong Claude Code package name in error message (`claude-cli` → `claude-code`)
  - Added transparent `/v1/*` API passthrough so Claude Code endpoints beyond `/v1/messages` don't 404
  - Fixed Sentry crash on invalid DSN by validating URL format before `sentry_sdk.init()`
  - Fixed gateway port instability by stopping old gateway before selecting a new port
  - Fixed Claude Code TUI freeze when launched via `curl | bash` by reopening stdin from the real pty device
  - Fixed 500 errors on non-streaming requests for Opus models by using streaming internally in `complete()`
