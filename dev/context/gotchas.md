# Gotchas

Non-obvious behaviors, edge cases, and things that are easy to get wrong.

---

## Testing

- E2E tests (`pytest -m e2e`) are SLOW - use sparingly, prefer unit tests for rapid iteration
- Always run `./scripts/dev_checks.sh` before committing - formats, lints, type-checks, and tests

## Docker Development

- Use `docker compose restart control-plane` or `litellm-proxy` to iterate on changes
- Check logs with `docker compose logs -f` when debugging

---

(Add gotchas as discovered)
