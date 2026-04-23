"""Guard: SQLite migration files must not use Postgres-only SQL constructs.

The SQLite migration runner executes each file via ``executescript()``, which
uses SQLite's native statement splitter (needed for trigger BEGIN...END blocks)
but bypasses the ``_translate_params`` rewriter. Any Postgres-only syntax that
the rewriter would normally translate will therefore reach SQLite verbatim and
fail at startup.

This test audits every ``migrations/sqlite/*.sql`` file for the patterns the
translator knows how to rewrite, plus a handful of Postgres-only keywords that
have no SQLite equivalent. Failures here mean either (a) the SQLite migration
file needs to be rewritten using SQLite-native syntax, or (b) the translator
needs to be integrated into the migration runner.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
SQLITE_MIGRATIONS_DIR = REPO_ROOT / "migrations" / "sqlite"
BUNDLED_MIGRATIONS_DIR = REPO_ROOT / "src" / "luthien_proxy" / "utils" / "sqlite_migrations"


# Patterns the _translate_params rewriter would transform for runtime queries
# but which executescript() lets through unchanged.
BANNED_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\$\d+", "asyncpg-style $N placeholder (use ? in DDL only if bound via execute())"),
    (r"::\w+", "Postgres ::type cast"),
    (r"\bNOW\s*\(\s*\)", "NOW() -- use datetime('now')"),
    (r"\bILIKE\b", "ILIKE -- use LIKE (SQLite LIKE is case-insensitive for ASCII)"),
    (r"\bLEAST\s*\(", "LEAST( -- use MIN("),
    (r"\bto_timestamp\s*\(", "to_timestamp( -- use datetime(?, 'unixepoch')"),
    # Additional Postgres-only constructs with no translator equivalent; if one
    # of these slips into a SQLite migration the DDL simply fails to apply.
    (r"\bCREATE\s+EXTENSION\b", "CREATE EXTENSION (Postgres-only)"),
    (r"\bCOMMENT\s+ON\b", "COMMENT ON (Postgres-only)"),
    (r"\bGRANT\b|\bREVOKE\b", "GRANT/REVOKE (Postgres-only)"),
    (r"\bCONCURRENTLY\b", "CREATE INDEX CONCURRENTLY (Postgres-only)"),
    (r"\bJSONB\b", "JSONB type (use TEXT)"),
    (r"\bTIMESTAMPTZ\b", "TIMESTAMPTZ (use TEXT)"),
    (r"\bUUID\b(?!\s*')", "UUID type (use TEXT)"),
)


def _strip_comments(sql: str) -> str:
    """Drop ``--`` line comments so they don't trigger false positives."""
    return re.sub(r"--[^\n]*", "", sql)


def _iter_migration_files() -> list[Path]:
    files = sorted(SQLITE_MIGRATIONS_DIR.glob("*.sql"))
    assert files, f"no sqlite migration files found under {SQLITE_MIGRATIONS_DIR}"
    return files


@pytest.mark.parametrize("migration_file", _iter_migration_files(), ids=lambda p: p.name)
def test_sqlite_migration_has_no_postgres_syntax(migration_file: Path) -> None:
    """Every checked-in SQLite migration uses only SQLite-native syntax."""
    body = _strip_comments(migration_file.read_text())
    violations: list[str] = []
    for pattern, label in BANNED_PATTERNS:
        for match in re.finditer(pattern, body, flags=re.IGNORECASE):
            line_no = body.count("\n", 0, match.start()) + 1
            violations.append(f"{migration_file.name}:{line_no}: {label} -- matched {match.group(0)!r}")
    assert not violations, "Postgres-only syntax in SQLite migration(s):\n  " + "\n  ".join(violations)


def test_bundled_migrations_match_source() -> None:
    """The bundled copy under ``src/.../sqlite_migrations`` mirrors the canonical files.

    The runbook requires copying every new ``migrations/sqlite/*.sql`` into the
    bundled directory so installed packages include the schema. Drift here is
    the most common way a migration gets applied locally but is missing for
    deployed wheels.
    """
    source_files = {p.name: p.read_bytes() for p in SQLITE_MIGRATIONS_DIR.glob("*.sql")}
    bundled_files = {p.name: p.read_bytes() for p in BUNDLED_MIGRATIONS_DIR.glob("*.sql")}

    missing = sorted(set(source_files) - set(bundled_files))
    extra = sorted(set(bundled_files) - set(source_files))
    mismatched = sorted(n for n in source_files if n in bundled_files and source_files[n] != bundled_files[n])

    problems: list[str] = []
    if missing:
        problems.append(f"missing from bundled: {missing}")
    if extra:
        problems.append(f"only in bundled: {extra}")
    if mismatched:
        problems.append(f"content differs: {mismatched}")
    assert not problems, "; ".join(problems)
