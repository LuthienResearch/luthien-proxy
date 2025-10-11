# Gotchas

Non-obvious behaviors, edge cases, and things that are easy to get wrong.

**Format**: Each entry is a subsection with a title, timestamp (YYYY-MM-DD), and content (bullet points).
If updating existing content significantly, note it: `## Topic (2025-10-08, updated 2025-11-15)`

---

## Testing (2025-10-08)

- E2E tests (`pytest -m e2e`) are SLOW - use sparingly, prefer unit tests for rapid iteration
- Always run `./scripts/dev_checks.sh` before committing - formats, lints, type-checks, and tests

## Docker Development (2025-10-08)

- Use `docker compose restart control-plane` or `litellm-proxy` to iterate on changes
- Check logs with `docker compose logs -f` when debugging
- Long-running compose or `uv` commands can hang the CLI; launch them via `scripts/run_bg_command.sh` so you can poll logs (`tail -f`) and terminate with the recorded PID if needed.

## Observability Checks (2025-10-08)

- Rely on the `tests/e2e_tests` helpers (see `policy_assertions.py`) for live integration checks; ad-hoc scripts were removed alongside the legacy trace APIs.

## Documentation Structure (2025-10-10)

**Gotcha**: Streaming does NOT write to `conversation_events` table (only debug_logs per-chunk, Redis at end)

- **Why it's confusing**: Non-streaming writes to three destinations (debug_logs, conversation_events, Redis), so developers expect streaming to do the same
- **Reality**: Streaming only writes to debug_logs per-chunk to avoid write amplification (1000-chunk response = 1000 DB rows)
- **What to know**: Per-chunk logs go to debug_logs only; at stream end, summary is published to Redis pub/sub (no DB write for conversation_events)
- **Where documented**: See `docs/diagrams.md#result-handling-pattern` for visual comparison
- **Code reference**: `streaming_routes.py:225` (`_StreamEventPublisher` class docstring explains this explicitly now)

**Gotcha**: Documentation references must be updated when renaming docs

- Old structure had `dataflows.md`, `reading-guide.md`, `dataflow-diagrams.md`
- New structure has `ARCHITECTURE.md`, `developer-onboarding.md`, `diagrams.md`
- Common places to check: README.md, tests/e2e_tests/CLAUDE.md, dev planning docs, inline code comments
- Easy to miss: Links in other markdown files, docstrings pointing to specific sections

---

(Add gotchas as discovered with timestamps: YYYY-MM-DD)
