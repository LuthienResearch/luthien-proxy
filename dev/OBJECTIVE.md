# Objective

Make dockerless local startup trivial with `--local` flag.

## Description

Starting luthien-proxy without Docker requires manually editing `.env` to fix Docker-internal hostnames. The app already supports SQLite and in-process Redis, but nothing makes this easy. We'll add a `--local` flag to `python -m luthien_proxy.main` that auto-configures sensible defaults for zero-dependency local mode.

## Approach

1. **Add `--local` flag to `__main__` block in `main.py`**: When `--local` is passed, set env var defaults before `load_config_from_env()` runs — SQLite DB, empty Redis URL, local policy config path, `file` policy source. Generate ephemeral API keys if not set and print them.

2. **Create `.env.local.example`**: A template file showing what local-mode env vars look like, so users can also do it manually without the flag.

3. **Update README**: Add a "Quick Start (no Docker)" section right after the existing Docker quick start.

4. **Unit tests**: Test the `--local` flag logic — env var defaulting, key generation, config loading.

## Test Strategy

- Unit tests: Test that `--local` sets correct env var defaults, generates API keys when missing, doesn't override existing env vars
- No e2e needed — the flag just sets env vars before existing code runs

## Acceptance Criteria

- [ ] `uv run python -m luthien_proxy.main --local` starts the gateway with zero .env editing
- [ ] Generated API keys are printed to stdout so the user knows them
- [ ] Existing env vars are not overwritten (setdefault semantics)
- [ ] `.env.local.example` exists as a template
- [ ] README has "no Docker" quick start section
- [ ] dev_checks passes

## Tracking

- Trello: https://trello.com/c/yPixh1UJ
- Branch: worktree-sleepy-enchanting-spindle
- PR: (filled later)
