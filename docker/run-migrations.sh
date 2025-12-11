#!/bin/sh
# ABOUTME: Runs all SQL migrations in order, tracking which have been applied
# ABOUTME: Idempotent - safe to run multiple times
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

# Create migrations tracking table if it doesn't exist
psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" <<EOF
CREATE TABLE IF NOT EXISTS _migrations (
    filename TEXT PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT NOW()
);
EOF

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
        psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -f "$migration"
        psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" <<EOF
INSERT INTO _migrations (filename) VALUES ('$filename');
EOF
        echo "✅ Applied: $filename"
    else
        echo "⏭️  Skipping (already applied): $filename"
    fi
done

echo "🎉 All migrations complete!"
