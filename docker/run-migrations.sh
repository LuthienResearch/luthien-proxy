#!/bin/sh
# ABOUTME: Runs all SQL migrations in order, tracking which have been applied
# ABOUTME: Idempotent - safe to run multiple times

set -e

echo "🔄 Running database migrations..."

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
for migration in /migrations/*.sql; do
    filename=$(basename "$migration")

    # Check if already applied (use parameterized query to prevent SQL injection)
    applied=$(psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -t -c \
        "SELECT COUNT(*) FROM _migrations WHERE filename = \$1;" -v "1=$filename" | tr -d ' ')

    if [ "$applied" = "0" ]; then
        echo "📦 Applying migration: $filename"
        psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -f "$migration"
        psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -c \
            "INSERT INTO _migrations (filename) VALUES (\$1);" -v "1=$filename"
        echo "✅ Applied: $filename"
    else
        echo "⏭️  Skipping (already applied): $filename"
    fi
done

echo "🎉 All migrations complete!"
