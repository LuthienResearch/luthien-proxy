#!/usr/bin/env python3
"""Forbidden-paths gate for the automated autofix bot.

The autofix session runs with `--permission-mode bypassPermissions` and
`Bash` in the tool list, so a misbehaving session could modify anything
the scheduler user can write. The gate is the post-session enforcement
that refuses to push diffs touching sensitive paths.

Categories blocked (in order of severity):

  - **Secrets**: any `.env*` file (`.env`, `.env.local`, `.env.example`,
    `.env.production`, `.envrc`, `foo.env`, etc.). `.env.example` is
    blocked deliberately — it's codegen output from
    `scripts/generate_env_example.py`; autofix should regenerate via
    `dev_checks.sh` rather than hand-edit.
  - **Migrations**: `migrations/**` — schema edits get reviewed by a
    human, full stop.
  - **The maintenance pipeline itself**: `scripts/automated_maintenance/**` and
    `tests/luthien_proxy/unit_tests/automated_maintenance/**`. A bot able to soften
    its own gates or tests is a meaningful failure mode.

Usage:
    python3 path_gate.py file1 file2 ...
    # Exits 0 if all paths are allowed.
    # Exits 2 if any path is blocked; prints blocked paths to stdout.
"""

from __future__ import annotations

import re
import sys

# Patterns are anchored against full repo-relative paths (forward slashes,
# no leading `/`). Each pattern is tested with `re.search`, so wrap with
# `^...$` if you mean exact match.
_FORBIDDEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Any `.env*` artefact: bare `.env`, `.envrc`, any `.env.<suffix>`
    # (including `.env.local`, `.env.example`, `.env.bak`), and any
    # filename ending in `.env` or containing `.env.` anywhere
    # (`foo.env`, `foo.env.bak`, `config/secrets.env.backup`, etc.).
    # The `.env.bak` variant is the conventional `sed -i.bak` output,
    # which an autofix session could otherwise smuggle past the gate.
    re.compile(r"(^|/)\.env($|\.|rc$)"),
    re.compile(r"(^|/)[^/]+\.env($|\.)"),
    # Migrations: any file under a top-level or nested `migrations/` dir.
    re.compile(r"(^|/)migrations/"),
    # The pipeline itself.
    re.compile(r"^scripts/automated_maintenance/"),
    # The maintenance pipeline's own tests.
    re.compile(r"^tests/luthien_proxy/unit_tests/automated_maintenance/"),
)


def _normalize(path: str) -> str:
    """Strip a leading `./` so anchored patterns match. `git diff
    --name-only` doesn't emit `./` prefixes today, but normalising here
    makes the gate robust if any future caller passes paths through
    `realpath` or `os.path.relpath` first.
    """
    return path[2:] if path.startswith("./") else path


def is_forbidden(path: str) -> bool:
    """Return True if ``path`` matches any forbidden pattern."""
    path = _normalize(path)
    return any(p.search(path) for p in _FORBIDDEN_PATTERNS)


def classify(paths: list[str]) -> list[str]:
    """Return the subset of ``paths`` that are blocked."""
    return [p for p in paths if is_forbidden(p)]


def main(argv: list[str]) -> int:
    blocked = classify(argv)
    for p in blocked:
        print(p)
    return 2 if blocked else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
