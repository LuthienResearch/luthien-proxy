"""Guard: migration filenames stay sane so the runners apply them correctly.

Both migration runners order *and* track applied state by **full filename**, not
by numeric prefix:

- SQLite (``utils/migration_check.py``): ``sorted(dir.glob("*.sql"))``; recorded in
  ``_migrations`` with ``filename TEXT PRIMARY KEY``.
- Postgres (``docker/run-migrations.sh``): bash ``*.sql`` glob (alphabetical);
  ``SELECT ... WHERE filename = '<name>'`` then apply-if-absent.

Two consequences this test protects:

1. **Duplicate numeric prefixes are a latent hazard.** Two files sharing a prefix
   both apply fine today, but their relative order then rides silently on the
   alphabetical *suffix* — if a future same-prefix pair has a real dependency that
   sorts the wrong way, it applies out of order with no warning. The repo already
   carries two such collisions (008, 014) from branches that each grabbed the same
   next integer and both merged. Those are grandfathered (see below); **new** ones
   are rejected here.

2. **Renumbering applied history is unsafe**, which is why the existing collisions
   are grandfathered rather than fixed: renaming an already-applied file would make
   the filename-keyed ``_migrations`` table treat it as a brand-new migration and
   re-run it on every existing deployment. The only safe remedy is to stop *new*
   collisions, which is what this guard does.

Also enforces cross-dialect parity on the prefix multiset (every Postgres migration
number has a SQLite counterpart and vice versa) and a consistent filename format.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
POSTGRES_DIR = REPO_ROOT / "migrations" / "postgres"
SQLITE_DIR = REPO_ROOT / "migrations" / "sqlite"

# 3-digit zero-padded prefix + snake_case description, e.g. 014_add_session_search_fts.sql
FILENAME_RE = re.compile(r"^\d{3}_[a-z][a-z0-9]*(?:_[a-z0-9]+)*\.sql$")

# Numeric prefixes that already collide on main and CANNOT be safely renumbered
# (``_migrations`` is filename-keyed; renaming an applied file re-runs it). New
# collisions outside this set are rejected. If a collision here is ever resolved
# safely, ``test_grandfathered_collisions_still_collide`` will fail to force the
# allowlist to shrink — it must not rot into a blanket exemption.
GRANDFATHERED_DUPLICATE_PREFIXES: frozenset[str] = frozenset({"008", "014"})

_DIALECT_DIRS: dict[str, Path] = {"postgres": POSTGRES_DIR, "sqlite": SQLITE_DIR}


def _sql_files(directory: Path) -> list[Path]:
    files = sorted(directory.glob("*.sql"))
    assert files, f"no migration files found under {directory}"
    return files


def _prefix(path: Path) -> str:
    return path.name.split("_", 1)[0]


def _all_files() -> list[Path]:
    return _sql_files(POSTGRES_DIR) + _sql_files(SQLITE_DIR)


@pytest.mark.parametrize("migration_file", _all_files(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_migration_filename_format(migration_file: Path) -> None:
    """Every migration is ``NNN_snake_case.sql`` with a 3-digit zero-padded prefix."""
    assert FILENAME_RE.match(migration_file.name), (
        f"{migration_file.parent.name}/{migration_file.name} does not match "
        f"the required `NNN_snake_case.sql` format (3-digit zero-padded prefix)."
    )


@pytest.mark.parametrize("dialect", sorted(_DIALECT_DIRS))
def test_no_new_duplicate_prefixes(dialect: str) -> None:
    """No two migrations in a dialect share a numeric prefix (except grandfathered)."""
    prefixes = Counter(_prefix(p) for p in _sql_files(_DIALECT_DIRS[dialect]))
    duplicates = {prefix for prefix, count in prefixes.items() if count > 1}
    new_duplicates = sorted(duplicates - GRANDFATHERED_DUPLICATE_PREFIXES)
    assert not new_duplicates, (
        f"migrations/{dialect}/ has new duplicate prefix(es): {new_duplicates}. "
        "Pick the next free number instead of reusing one — duplicate prefixes make "
        "apply-order depend silently on the alphabetical suffix. Do NOT renumber an "
        "already-applied migration (the _migrations table is filename-keyed and would "
        "re-run it). If you believe a collision is unavoidable, add it to "
        "GRANDFATHERED_DUPLICATE_PREFIXES with justification."
    )


def test_grandfathered_collisions_still_collide() -> None:
    """Every grandfathered prefix must actually still collide in some dialect.

    Keeps the allowlist honest: if a collision is ever resolved, this fails so the
    entry gets removed rather than silently masking a future real collision.
    """
    colliding: set[str] = set()
    for directory in _DIALECT_DIRS.values():
        counts = Counter(_prefix(p) for p in _sql_files(directory))
        colliding |= {prefix for prefix, count in counts.items() if count > 1}
    stale = sorted(GRANDFATHERED_DUPLICATE_PREFIXES - colliding)
    assert not stale, (
        f"grandfathered prefix(es) no longer collide: {stale}. Remove them from GRANDFATHERED_DUPLICATE_PREFIXES."
    )


def test_cross_dialect_prefix_parity() -> None:
    """Postgres and SQLite carry the same multiset of migration prefixes.

    A Postgres migration without a same-numbered SQLite counterpart (or vice versa)
    is the classic "added one dialect, forgot the other" mistake.
    """
    postgres = Counter(_prefix(p) for p in _sql_files(POSTGRES_DIR))
    sqlite = Counter(_prefix(p) for p in _sql_files(SQLITE_DIR))
    only_postgres = sorted((postgres - sqlite).elements())
    only_sqlite = sorted((sqlite - postgres).elements())
    problems: list[str] = []
    if only_postgres:
        problems.append(f"prefixes in postgres but not sqlite: {only_postgres}")
    if only_sqlite:
        problems.append(f"prefixes in sqlite but not postgres: {only_sqlite}")
    assert not problems, "; ".join(problems)
