# Objective

Move dev tools (pyright, vulture, pytest-timeout) from production to dev dependencies.

## Description

`pyright`, `vulture`, and `pytest-timeout` are listed in `[project.dependencies]` (shipped in production installs) instead of the `[dependency-groups] dev` group. These are development-only tools that shouldn't be pulled in when users `pip install luthien-proxy`.

Note: `pyright` is already in the dev group with version constraints (`>=1.1.406,<1.2`), so it just needs removal from production deps. `pytest-timeout` and `vulture` need to be moved.

## Approach

1. Remove `pyright`, `pytest-timeout`, and `vulture` from `[project.dependencies]`
2. Add `pytest-timeout` and `vulture` to `[dependency-groups] dev` (pyright already there)
3. Run `uv sync --dev` to verify deps resolve
4. Run `scripts/dev_checks.sh` to verify dev workflow unchanged

## Test Strategy

- No new tests needed — this is a packaging/dependency change
- Verify with `uv sync --dev` and `scripts/dev_checks.sh`

## Acceptance Criteria

- [ ] pyright, vulture, pytest-timeout removed from `[project.dependencies]`
- [ ] vulture and pytest-timeout added to `[dependency-groups] dev`
- [ ] `uv sync --dev` succeeds
- [ ] `scripts/dev_checks.sh` passes
- [ ] dev workflow unchanged

## Tracking

- Trello: none
- Branch: worktree-wise-chasing-pillow
- PR: TBD
