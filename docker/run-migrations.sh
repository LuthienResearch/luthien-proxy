#!/bin/sh
# Requires: POSIX sh (runs in Alpine Docker containers)
# ABOUTME: Runs all SQL migrations in order, tracking which have been applied
# ABOUTME: Validates migration consistency before applying (fail-fast on drift)
# ABOUTME: Works in Docker (/migrations/) or CI (set MIGRATIONS_DIR=./migrations)

set -e

# Default to /migrations for Docker, can be overridden for CI
MIGRATIONS_DIR="${MIGRATIONS_DIR:-/migrations}"

echo "🔄 Running database migrations from $MIGRATIONS_DIR..."

# Wait for database to be ready
until pg_isready -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE"; do
  echo "⏳ Waiting for database..."
  sleep 2
done

echo "✅ Database is ready"

# Create migrations tracking table if it doesn't exist (with content_hash for validation)
psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" <<EOF
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
# VALIDATION PHASE: Fail fast if migrations are inconsistent
# ============================================================================
echo "🔍 Validating migration consistency..."

# Get all migrations recorded in the database
db_migrations=$(psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -t -A <<EOF
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

    local_file="$MIGRATIONS_DIR/$db_filename"

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

# Apply each migration in order
for migration in "$MIGRATIONS_DIR"/*.sql; do
    filename=$(basename "$migration")

    # Check if already applied
    applied=$(psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -t <<EOF | tr -d ' '
SELECT COUNT(*) FROM _migrations WHERE filename = '$filename';
EOF
)

    if [ "$applied" = "0" ]; then
        echo "📦 Applying migration: $filename"
        content_hash=$(compute_hash "$migration")
        psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -f "$migration"
        psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" <<EOF
INSERT INTO _migrations (filename, content_hash) VALUES ('$filename', '$content_hash');
EOF
        echo "✅ Applied: $filename (hash: $content_hash)"
    else
        echo "⏭️  Skipping (already applied): $filename"
    fi
done

echo "🎉 All migrations complete!"
