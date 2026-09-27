#!/bin/sh
# ABOUTME: Runs all SQL migrations in order, tracking which have been applied
# ABOUTME: Validates migration consistency before applying (fail-fast on drift)
# ABOUTME: Works in Docker (/migrations/) or CI (set MIGRATIONS_DIR=./migrations)

set -e

# Default to /migrations for Docker, can be overridden for CI
MIGRATIONS_DIR="${MIGRATIONS_DIR:-/migrations}"

# Auto-detect postgres subdirectory
POSTGRES_DIR="$MIGRATIONS_DIR/postgres"
if [ -d "$POSTGRES_DIR" ]; then
    MIGRATIONS_SQL_DIR="$POSTGRES_DIR"
else
    MIGRATIONS_SQL_DIR="$MIGRATIONS_DIR"
fi

echo "🔄 Running database migrations from $MIGRATIONS_SQL_DIR..."

# Wait for database to be ready
until pg_isready -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE"; do
  echo "⏳ Waiting for database..."
  sleep 2
done

echo "✅ Database is ready"

# Every psql call in this script goes through this wrapper. Without
# `-v ON_ERROR_STOP=1`, psql prints a failing statement's error, continues on
# to the rest of the script, and still exits 0 -- so a migration that half
# failed would fall straight through to the "record as applied" step below.
# `set -e` only helps once psql itself reports failure; ON_ERROR_STOP is what
# makes it do so.
run_psql() {
    psql -v ON_ERROR_STOP=1 -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" "$@"
}

# Create migrations tracking table if it doesn't exist (with content_hash for validation)
run_psql <<EOF
CREATE TABLE IF NOT EXISTS _migrations (
    filename TEXT PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT NOW(),
    content_hash TEXT
);

-- Add content_hash column if it doesn't exist (for existing installations)
DO \$\$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = '_migrations' AND column_name = 'content_hash'
    ) THEN
        ALTER TABLE _migrations ADD COLUMN content_hash TEXT;
    END IF;
END
\$\$;
EOF

# Compute hash for a migration file (using md5sum, available in alpine)
compute_hash() {
    md5sum "$1" | cut -d' ' -f1
}

# ============================================================================
# ONE-TIME FIXUPS: Update hashes for migrations with known-safe content changes
# ============================================================================
# PR #281 removed a hardcoded `\c luthien_control` from migration 008 that
# broke Railway deployments. The schema effect is identical — only the connect
# command and GRANT wrapper changed. Update the stored hash so validation passes.
OLD_008_HASH="49ad0e5ce7fc13692a5300ed30f1a96e"
NEW_008_HASH="76cfa2f26a6925f00ca10e76159bb2ea"
run_psql -q <<EOF
UPDATE _migrations
   SET content_hash = '$NEW_008_HASH'
 WHERE filename = '008_add_request_logs_table.sql'
   AND content_hash = '$OLD_008_HASH';
EOF

# ============================================================================
# ONE-TIME CLEANUP: Remove sqlite_schema.sql tracking from Postgres databases
# TODO: Remove this block after all deployments have run it (added 2026-03)
# ============================================================================
# The sqlite_schema.sql file has moved to migrations/sqlite/ and is no longer
# in the Postgres migrations directory. Remove the stale tracking row to prevent
# "file not found locally" validation errors.
run_psql -q <<EOF
DELETE FROM _migrations WHERE filename = 'sqlite_schema.sql';
EOF

# ============================================================================
# VALIDATION PHASE: Fail fast if migrations are inconsistent
# ============================================================================
echo "🔍 Validating migration consistency..."

# Get all migrations recorded in the database
db_migrations=$(run_psql -t -A <<EOF
SELECT filename, COALESCE(content_hash, '') FROM _migrations ORDER BY filename;
EOF
)

# Process each DB migration
for line in $db_migrations; do
    # Skip empty lines
    [ -z "$line" ] && continue

    db_filename=$(echo "$line" | cut -d'|' -f1)
    db_hash=$(echo "$line" | cut -d'|' -f2)

    # Skip if filename is empty
    [ -z "$db_filename" ] && continue

    local_file="$MIGRATIONS_SQL_DIR/$db_filename"

    # Check 1: All DB migrations must exist locally
    if [ ! -f "$local_file" ]; then
        echo "❌ MIGRATION ERROR: Database has migration '$db_filename' but file not found locally!"
        echo "   This usually means you're on a branch missing migrations from the database."
        echo "   Options:"
        echo "     1. Switch to a branch that has this migration"
        echo "     2. Pull latest changes that include this migration"
        echo "     3. Reset your dev database: docker compose down -v && docker compose up -d"
        exit 1
    fi

    # Check 2: Applied migrations must have matching content (if hash was recorded)
    if [ -n "$db_hash" ]; then
        local_hash=$(compute_hash "$local_file")
        if [ "$db_hash" != "$local_hash" ]; then
            echo "❌ MIGRATION ERROR: Content mismatch for '$db_filename'!"
            echo "   DB hash:    $db_hash"
            echo "   Local hash: $local_hash"
            echo "   The migration file was modified after being applied to the database."
            echo "   This is dangerous and can cause schema drift."
            echo "   Options:"
            echo "     1. Revert your local changes to the migration file"
            echo "     2. Create a new migration for your schema changes"
            echo "     3. Reset your dev database: docker compose down -v && docker compose up -d"
            exit 1
        fi
    fi
done

echo "✅ Migration validation passed"

# ============================================================================
# APPLICATION PHASE: Apply pending migrations
# ============================================================================

# A `CREATE INDEX CONCURRENTLY` build that gets interrupted (dropped
# connection, OOM kill, crashed backend) leaves an INVALID index behind under
# its target name -- that failure is real and already aborts this script
# (psql reports the dropped connection as an error). But a *subsequent* run of
# the same migration file uses `CREATE INDEX CONCURRENTLY IF NOT EXISTS`,
# which matches by name only: it finds the (invalid) name already taken,
# prints a NOTICE, and reports success with exit code 0. ON_ERROR_STOP cannot
# catch this because psql never sees an error on that run. So after a
# migration applies cleanly, verify that any index it builds CONCURRENTLY is
# actually valid before recording the migration as applied -- otherwise a
# retried deploy would silently record success while shipping an index
# Postgres itself refuses to use.
check_concurrent_indexes_valid() {
    migration_file="$1"
    index_names=$(sed 's/--.*$//' "$migration_file" \
        | grep -Eio 'create[[:space:]]+(unique[[:space:]]+)?index[[:space:]]+concurrently[[:space:]]+(if[[:space:]]+not[[:space:]]+exists[[:space:]]+)?[a-z_][a-z0-9_]*' \
        | awk '{print $NF}')
    [ -z "$index_names" ] && return 0

    for index_name in $index_names; do
        is_valid=$(run_psql -t -A -c "SELECT indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = '$index_name';")
        if [ "$is_valid" != "t" ]; then
            echo "❌ MIGRATION ERROR: index '$index_name' from '$(basename "$migration_file")' is not valid (indisvalid=$is_valid)!"
            echo "   CREATE INDEX CONCURRENTLY IF NOT EXISTS matches by name only, so a"
            echo "   previously failed/interrupted concurrent build leaves an INVALID index"
            echo "   that silently 'succeeds' on retry without ever becoming usable."
            echo "   Drop the invalid index and re-run this migration:"
            echo "     DROP INDEX CONCURRENTLY IF EXISTS $index_name;"
            exit 1
        fi
    done
}

# Apply each migration in order
for migration in "$MIGRATIONS_SQL_DIR"/*.sql; do
    filename=$(basename "$migration")

    # Check if already applied
    applied=$(run_psql -t -A -c "SELECT COUNT(*) FROM _migrations WHERE filename = '$filename';")

    if [ "$applied" = "0" ]; then
        echo "📦 Applying migration: $filename"
        content_hash=$(compute_hash "$migration")
        run_psql -f "$migration"
        check_concurrent_indexes_valid "$migration"
        run_psql <<EOF
INSERT INTO _migrations (filename, content_hash) VALUES ('$filename', '$content_hash');
EOF
        echo "✅ Applied: $filename (hash: $content_hash)"
    else
        echo "⏭️  Skipping (already applied): $filename"
    fi
done

echo "🎉 All migrations complete!"
