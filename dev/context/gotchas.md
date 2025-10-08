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

- To verify control-plane /ui pages and related APIs after changes, run `scripts/check_ui_endpoints.py` (wrap with `scripts/run_bg_command.sh …` if you want background execution). It fires a streaming request, waits for ingestion, and asserts all UI endpoints are returning 200s.

---

(Add gotchas as discovered with timestamps: YYYY-MM-DD)
