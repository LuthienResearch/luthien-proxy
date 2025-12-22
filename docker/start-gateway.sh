#!/bin/bash
# Startup script for Luthien Gateway
# Runs migrations (if DATABASE_URL is set) then starts the gateway

set -e

echo "========================================="
echo "Luthien Gateway Startup"
echo "========================================="
echo "Checking environment variables..."
echo "DATABASE_URL set: $([ -n \"$DATABASE_URL\" ] && echo 'YES' || echo 'NO')"
echo "REDIS_URL set: $([ -n \"$REDIS_URL\" ] && echo 'YES' || echo 'NO')"
echo "PROXY_API_KEY set: $([ -n \"$PROXY_API_KEY\" ] && echo 'YES' || echo 'NO')"
echo "ADMIN_API_KEY set: $([ -n \"$ADMIN_API_KEY\" ] && echo 'YES' || echo 'NO')"
echo "GATEWAY_PORT: ${GATEWAY_PORT:-not set}"
echo "PORT (Railway): ${PORT:-not set}"
echo "========================================="

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
else
    echo "WARNING: DATABASE_URL not set, skipping migrations"
fi

# Start the gateway
echo "Starting gateway application..."
echo "Command: uv run python -m luthien_proxy.main"
exec uv run python -m luthien_proxy.main
