# Session: 2026-03-14 — Sentry Integration + Error Handling Analysis

## What Was Done

### 1. Sentry Integration (PR #335 — feat/sentry-integration)
- **Branch**: `feat/sentry-integration` (5 commits, pushed to origin)
- **PR**: https://github.com/LuthienResearch/luthien-proxy/pull/335
- **Status**: Review feedback addressed, ready for merge

**Changes:**
- `pyproject.toml` / `uv.lock` — added `sentry-sdk[fastapi]`
- `settings.py` — `sentry_enabled` (default True), `sentry_dsn` (hardcoded project DSN), `sentry_traces_sample_rate` (0.0), `sentry_server_name`
- `main.py:56-153` — Module-level Sentry init with two-layer data scrubbing:
  - Layer 1: Extended `EventScrubber` (credential key-name matching)
  - Layer 2: `before_send` hook (selective LLM content redaction, keeps debugging context)
- `tests/conftest.py` — `ENVIRONMENT=test` (NOT disabled — deliberate decision)
- `.env.example` — Documented opt-out
- `saas_infra/provisioner.py` — Railway gets `ENVIRONMENT=railway` + `SENTRY_SERVER_NAME=railway-{name}`
- `dev/context/sentry.md` — Full reference documentation
- `dev/context/decisions.md` — Decision record added
- `tests/unit_tests/test_sentry_scrubbing.py` — 30 tests

**Key Design Decisions:**
- Opt-out (not opt-in) — matches USAGE_TELEMETRY pattern
- DSN hardcoded in settings.py — write-only key, safe to publish
- send_default_pii=False
- Sentry stays active in tests — tagged environment=test, filter in dashboard
- Reviewer suggested disabling Sentry in tests; Paolo explicitly rejected this

### 2. Real Bug Found by Sentry
- JSONDecodeError in `anthropic_processor.py:430` — `await request.json()` with no try/except
- Malformed JSON returns raw 500 instead of 400
- Same bug in OpenAI path (`processor.py:249`)
- **Trello**: https://trello.com/c/t3cAj9ls (In Progress, due Mar 18, High Priority)

### 3. Global Exception Handler Analysis (NOT implemented yet)
- **Trello**: https://trello.com/c/vFYAmbvD (In Progress, due Mar 19, High Priority)
- Three handlers needed in main.py:
  - `@app.exception_handler(StarletteHTTPException)` — reformats HTTPExceptions
  - `@app.exception_handler(RequestValidationError)` — reformats Pydantic errors
  - `@app.exception_handler(Exception)` — catch-all for unhandled crashes
- Route-path-based format switching (SGLang pattern):
  - `/v1/messages` → Anthropic format
  - `/v1/chat/completions` → OpenAI format
  - Everything else → FastAPI default
- Subsumes HTTPException format mismatch + error detail leakage cards
- ~40 lines in main.py

### 4. Trello Cleanup
- Fixed 5 cards with missing labels
- Archived 21 stale website board cards
- Moved merged HTTPException card to Done
- All Sentry noise issues resolved

## What's Next (Tomorrow)

1. **Merge PR #335** (Sentry) — review feedback addressed
2. **Fix JSONDecodeError bug** (t3cAj9ls, due Mar 18) — wrap `request.json()` in both processors
3. **Implement global exception handlers** (vFYAmbvD, due Mar 19) — analysis done, needs implementation

## Files on feat/sentry-integration branch
```
pyproject.toml, uv.lock                    # dependency
src/luthien_proxy/settings.py               # 4 Sentry settings
src/luthien_proxy/main.py                   # ~100 lines: init + scrubbing
tests/conftest.py                           # ENVIRONMENT=test
.env.example                                # opt-out docs
saas_infra/provisioner.py                   # Railway config
dev/context/sentry.md                       # NEW reference doc
dev/context/decisions.md                    # Sentry decision record
dev/context/README.md                       # added sentry.md
tests/unit_tests/test_sentry_scrubbing.py   # NEW 30 tests
```
