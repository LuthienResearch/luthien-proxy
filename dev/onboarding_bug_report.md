# Onboarding Bug Report — QA Feedback Round 1

**Branch:** `fix/claude-code-hang-after-trust`
**Date:** 2026-04-01
**Source:** Luthien Onboarding Feedback spreadsheet (tabs: User 1, User 2, User 3, User 4)

---

## Bug 1: Git operation failed during install (missing Xcode CLI tools)

**Reporter:** User 1 (Row 3)

**Symptom:**
```
error: Git operation failed
  Caused by: process didn't exit successfully: '/usr/bin/git init' (exit status: 1)
--- stderr
xcode-select: note: No developer tools found, requesting install.
```

**Root cause:** The install script uses `git+https://...` to install `luthien-cli` via `uv`, which requires a working `git`. On macOS without Xcode Command Line Tools, `/usr/bin/git` is a shim that fails silently when stdin is piped (`curl | bash`).

**Introduced by:**
- **Commit:** `b467a81d` — "fix: CLI install scripts and CI workflows broken since cli-v0.1.7"
- **PR:** #404 (`0cbf5e37`)
- **File:** `scripts/install.sh`
- **Context:** Changed from PyPI install (`uv tool install luthien-cli`) to `git+https://` install because PyPI version was stale. No git availability check was added.

**Fixed by:**
- **Commit:** `485d4ce5` — "fix: install script git check + correct Claude Code package name"
- **File:** `scripts/install.sh` (lines 10–29)
- **Fix:** Added early `git --version` check before `uv tool install`. On macOS, prints clear message to run `xcode-select --install`. Exits with status 1 on failure.

**Status:** FIXED

---

## Bug 2: Wrong Claude Code package name in error message

**Reporter:** User 1 (Row 16)

**Symptom:**
```
Claude Code CLI not found. Install: npm install -g @anthropic-ai/claude-cli.
This package is outdated. Use this one -> npm install @anthropic-ai/sdk
```

**Root cause:** The error message referenced `@anthropic-ai/claude-cli` instead of the correct package `@anthropic-ai/claude-code`.

**Introduced by:**
- **Commit:** `a305a2fe` — "feat(cli): add luthien claude command"
- **PR:** #388 (`e69e7fb2` — "feat: onboarding policy + streamlined CLI flow")
- **File:** `src/luthien_cli/src/luthien_cli/commands/claude.py`
- **Context:** Wrong package name used from initial creation of the `luthien claude` command.

**Fixed by:**
- **Commit:** `485d4ce5` — "fix: install script git check + correct Claude Code package name"
- **File:** `src/luthien_cli/src/luthien_cli/commands/claude.py` (line 23)
- **Fix:** Changed `@anthropic-ai/claude-cli` → `@anthropic-ai/claude-code`.

**Status:** FIXED

---

## Bug 3: Claude Code hangs after trust prompt (500 on non-/messages endpoints)

**Reporter:** User 2 (full bug report), User 1 (Row 4 — 500 error)

**Symptom:** After selecting "Yes, I trust this folder" in Claude Code, the session hangs indefinitely — no output, no error, no prompt. User 1 also observed:
```
500 {"type":"error","error":{"type":"api_error","message":"An internal error occurred while processing the request."}}
```

**Root cause:** Claude Code makes API calls to endpoints beyond `/v1/messages` (e.g., `/v1/messages/count_tokens`, `/v1/models`). The proxy only had a handler for `/v1/messages` and returned 404/500 for everything else, causing Claude Code to hang waiting for a valid response.

**Introduced by:**
- **Commit:** `b04d6cdf` — "v3: Integrated Architecture + Event-Based Policies"
- **Consolidated in:** `fc2a0c50` / PR #55 ("Remove v2 as a concept")
- **File:** `src/luthien_proxy/gateway_routes.py`
- **Context:** Gateway routes were designed with only explicit handlers for `/v1/messages` (and originally `/v1/chat/completions`). No catch-all route was added for other API endpoints. This gap persisted through every subsequent refactor (OpenAI removal in PR #351, etc.).

**Fixed by:**
- **Commit:** `12483a92` — "fix: prevent Claude Code hang by adding transparent API proxying"
- **Files:** `src/luthien_proxy/gateway_routes.py` (lines 172–218), `src/luthien_proxy/llm/anthropic_client.py`, `src/luthien_proxy/pipeline/anthropic_processor.py`
- **Fix:** Added catch-all route `@router.api_route("/v1/{path:path}", ...)` that transparently proxies any `/v1/*` request not matching `/v1/messages` to `https://api.anthropic.com`, forwarding auth headers, body, and query params.

**Status:** FIXED

---

## Bug 4: Sentry DSN="n" crashes gateway on startup

**Reporter:** User 3

**Symptom:**
```
Starting gateway on port 8000...
Gateway started (PID 16405)
Gateway did not become healthy within 60s
Check logs: luthien logs
```
The `.env` file contained `SENTRY_ENABLED=true` and `SENTRY_DSN=n`. The value `"n"` came from answering "n" to the Sentry Y/N prompt during onboarding — the "n" keypress was captured as the DSN value. `sentry_sdk.init()` threw `BadDsn: Unsupported scheme ''` on this invalid DSN, crashing the gateway before it could start listening.

**Introduced by:**
- **Commit:** `e784a779` / `40fc8507` — "feat: add Sentry opt-in prompt to onboard flow"
- **PR:** #335 (`4481eb7b` — "feat: integrate Sentry error tracking with two-layer data scrubbing")
- **Files:** `src/luthien_cli/src/luthien_cli/commands/onboard.py`, `src/luthien_proxy/observability/sentry.py`
- **Context:** The onboard flow added `click.confirm("Enable Sentry", default=False)` followed by `click.prompt("Sentry DSN", default="")`. The interactive prompt handling allowed the "n" answer to leak into the DSN field, and `sentry.py` had no validation to reject non-URL DSN values.

**Fixed by:**
- **Commit:** `4f6ce9b8` — "more fixes"
- **Files:**
  - `src/luthien_cli/src/luthien_cli/commands/onboard.py` — Removed the interactive Sentry prompt entirely. Sentry is now off by default; users enable it post-install by manually editing `.env`.
  - `src/luthien_proxy/observability/sentry.py` — Added URL validation: `sentry_dsn.startswith(("https://", "http://"))`. Invalid DSN values log a warning and skip Sentry initialization instead of crashing.

**Status:** FIXED

---

## Bug 5: Gateway port instability during onboarding causes Claude Code freeze

**Reporter:** User 4

**Symptom:** After running the install script and pressing enter through all prompts, Claude Code launches but freezes. Diagnostic showed the gateway was restarting and switching ports (8000 → 8001 → 8000 → 8001) during the onboarding flow, so Claude Code connected to a port that then went away.

**Root cause:** In `_onboard_local()`, `find_free_port()` was called *before* `stop_gateway()`. If an old gateway was still running on port 8000, `find_free_port` would select 8001. Then `stop_gateway` freed 8000. The new gateway started on 8001 but the timing window caused instability — Claude Code could connect to either port during the race.

**Introduced by:**
- **Commit:** `c03a9689` — "fix: local Docker build fallback when GHCR pull fails (#455)" (or earlier onboard refactors)
- **PR:** #455
- **File:** `src/luthien_cli/src/luthien_cli/commands/onboard.py`
- **Context:** The ordering of `stop_gateway()` and `find_free_port()` was not carefully sequenced — stop happened after port selection.

**Fixed by:**
- **Commit:** `4f6ce9b8` — "more fixes"
- **File:** `src/luthien_cli/src/luthien_cli/commands/onboard.py`
- **Fix:** Reordered `_onboard_local()` to call `stop_gateway()` *before* `find_free_port()`, so the old gateway's port is freed before selecting a new one. This eliminates the port-switching race condition.

**Status:** FIXED

---

## Summary

| # | Bug | Reporter | Introduced In | Fixed In | Status |
|---|-----|----------|---------------|----------|--------|
| 1 | Git check missing in install script | User 1 | PR #404 (`b467a81d`) | `485d4ce5` | FIXED |
| 2 | Wrong Claude Code package name | User 1 | PR #388 (`a305a2fe`) | `485d4ce5` | FIXED |
| 3 | Claude Code hang (no transparent proxying) | User 2, User 1 | `b04d6cdf` / PR #55 | `12483a92` | FIXED |
| 4 | Sentry DSN="n" crashes gateway | User 3 | PR #335 (`e784a779`) | `4f6ce9b8` | FIXED |
| 5 | Port instability during onboard | User 4 | PR #455 (`c03a9689`) | `4f6ce9b8` | FIXED |

---

## Bug 6: Claude Code TUI freezes after onboard when launched via `curl | bash`

**Reporter:** User 1, external tester

**Symptom:** After `luthien onboard` completes (gateway healthy, "Ready" panel shown) and the user presses a key to launch Claude Code, the terminal freezes in raw mode. Claude Code's TUI enters raw mode but never renders or accepts input. Running `luthien claude` separately from a fresh terminal works fine.

**Root cause:** When the install script runs via `curl | bash`, bash's stdin is a pipe. The script redirects with `luthien onboard </dev/tty`, which opens `/dev/tty` for fd 0. However, `/dev/tty` is an **indirect device node** (device 2,0) — not the actual pty device (e.g., `/dev/ttys000`, device 16,x). Claude Code runs on Bun, which uses macOS `kqueue`/`kevent64` for input polling. kqueue cannot monitor the indirect `/dev/tty` node — only real pty devices. So kevent silently returns no events for stdin, and the TUI freezes.

**Introduced by:**
- **Commit:** `a305a2fe` — "feat(cli): add luthien claude command"
- **PR:** #388 (`e69e7fb2` — "feat: onboarding policy + streamlined CLI flow")
- **File:** `src/luthien_cli/src/luthien_cli/commands/claude.py`
- **Context:** `os.execvpe("claude", ...)` inherits fd 0 from the parent process. When the parent's fd 0 is `/dev/tty` (from shell redirect), the exec'd Bun process inherits an fd that kqueue can't poll.

**Fixed by:**
- **Commit:** `16d79a66` — "fix: use real pty device path instead of /dev/tty for stdin dup2"
- **File:** `src/luthien_cli/src/luthien_cli/commands/claude.py`
- **Fix:** Before `os.execvpe`, get the real pty path via `os.ttyname(1)` (from stdout, which is already a proper pty fd), open it `O_RDWR`, and `dup2` onto fd 0 only. This gives Claude Code a real pty device that kqueue can monitor. fd 1/2 are left untouched — dup2'ing `/dev/tty` onto them crashes Bun's kqueue with EINVAL.

**Status:** TESTING — awaiting tester confirmation

---

## Bug 7: 500 errors on non-streaming requests for Opus models

**Reporter:** External tester

**Symptom:** Claude Code sends some requests with `stream=false` (or without the stream field) for `claude-opus-4-6`. The proxy forwards these non-streaming requests to the Anthropic API, which rejects them because Opus requires streaming. This causes 500 errors from the proxy.

**Root cause:** The proxy's non-streaming path calls `self._anthropic_client.complete()` which uses the Anthropic SDK's non-streaming endpoint. The Anthropic API requires streaming for Opus models and rejects non-streaming requests. Claude Code sends non-streaming requests for certain internal operations (e.g., token counting retries), so the proxy must handle this case.

**Introduced by:**
- **Context:** The Anthropic API's streaming requirement for Opus models was not accounted for in the proxy's non-streaming request path. The proxy faithfully forwards `stream=false` and gets rejected.

**Fix direction:** The proxy should detect when a model requires streaming and internally force streaming even when the client sends `stream=false`. Accumulate the streamed response, then return it as a single non-streaming JSON response to the client.

**Status:** OPEN — needs implementation

---

## Summary

| # | Bug | Reporter | Introduced In | Fixed In | Status |
|---|-----|----------|---------------|----------|--------|
| 1 | Git check missing in install script | User 1 | PR #404 (`b467a81d`) | `485d4ce5` | FIXED |
| 2 | Wrong Claude Code package name | User 1 | PR #388 (`a305a2fe`) | `485d4ce5` | FIXED |
| 3 | Claude Code hang (no transparent proxying) | User 2, User 1 | `b04d6cdf` / PR #55 | `12483a92` | FIXED |
| 4 | Sentry DSN="n" crashes gateway | User 3 | PR #335 (`e784a779`) | `4f6ce9b8` | FIXED |
| 5 | Port instability during onboard | User 4 | PR #455 (`c03a9689`) | `4f6ce9b8` | FIXED |
| 6 | Claude Code TUI freeze (kqueue + /dev/tty) | User 1, external | PR #388 (`a305a2fe`) | `16d79a66` | TESTING |
| 7 | 500 on non-streaming Opus requests | External tester | — | — | OPEN |

### Not addressed (out of scope)

| Issue | Reporter | Notes |
|-------|----------|-------|
| `warning: Failed to patch the install name of the dynamic library` | User 1 (Row 2) | Upstream uv/Python issue. Cosmetic warning, does not block install. |
