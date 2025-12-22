#!/bin/bash
# Startup script for Luthien Gateway
# Runs migrations (if DATABASE_URL is set) then starts the gateway

# Output to both stdout and stderr for Railway logging
exec 2>&1

echo "=========================================" >&2
echo "Luthien Gateway Startup" >&2
echo "=========================================" >&2
echo "Checking environment variables..." >&2
echo "DATABASE_URL set: $([ -n "$DATABASE_URL" ] && echo 'YES' || echo 'NO')" >&2
echo "REDIS_URL set: $([ -n "$REDIS_URL" ] && echo 'YES' || echo 'NO')" >&2
echo "PROXY_API_KEY set: $([ -n "$PROXY_API_KEY" ] && echo 'YES' || echo 'NO')" >&2
echo "ADMIN_API_KEY set: $([ -n "$ADMIN_API_KEY" ] && echo 'YES' || echo 'NO')" >&2
echo "GATEWAY_PORT: ${GATEWAY_PORT:-not set}" >&2
echo "PORT (Railway): ${PORT:-not set}" >&2
echo "PWD: $(pwd)" >&2
echo "whoami: $(whoami)" >&2
echo "=========================================" >&2

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
else
    echo "WARNING: DATABASE_URL not set, skipping migrations"
fi

# Start the gateway
echo "Starting gateway application..."
echo "Command: uv run python -m luthien_proxy.main"
exec uv run python -m luthien_proxy.main
