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

---

(Add gotchas as discovered with timestamps: YYYY-MM-DD)
