#!/bin/bash
# Startup script for Luthien Gateway
# Runs migrations (if DATABASE_URL is set) then starts the gateway

set -e

# Parse DATABASE_URL into individual components if set
# Format: postgresql://user:password@host:port/database
if [ -n "$DATABASE_URL" ]; then
    # Extract components using bash string manipulation
    # Remove protocol prefix
    DB_STRING="${DATABASE_URL#*://}"

    # Extract user:password@host:port/database
    USERPASS="${DB_STRING%%@*}"
    HOSTPORTDB="${DB_STRING#*@}"

    export PGUSER="${USERPASS%%:*}"
    export PGPASSWORD="${USERPASS#*:}"

    HOSTPORT="${HOSTPORTDB%%/*}"
    DBPART="${HOSTPORTDB#*/}"

    # Strip query parameters from database name (e.g., ?sslmode=require)
    export PGDATABASE="${DBPART%%\?*}"

    export PGHOST="${HOSTPORT%%:*}"
    export PGPORT="${HOSTPORT#*:}"

    # Handle case where port is not specified
    if [ "$PGPORT" = "$PGHOST" ]; then
        export PGPORT="5432"
    fi

    echo "Running database migrations..."
    export MIGRATIONS_DIR=/app/migrations
    /app/docker/run-migrations.sh
    echo "Migrations complete."
fi

# Start the gateway
exec uv run python -m luthien_proxy.main
