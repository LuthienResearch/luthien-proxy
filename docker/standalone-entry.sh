#!/bin/bash
# Startup script for the standalone (all-in-one) Luthien container.
# Starts PostgreSQL, Redis, runs migrations, then starts the gateway.

set -e

PG_BIN="/usr/lib/postgresql/16/bin"
PGDATA="/var/lib/postgresql/data"

# ── Graceful shutdown ─────────────────────────────────────────────
cleanup() {
    echo "==> Shutting down services..."
    kill "$GATEWAY_PID" 2>/dev/null || true
    wait "$GATEWAY_PID" 2>/dev/null || true
    redis-cli shutdown nosave 2>/dev/null || true
    su -s /bin/bash postgres -c "$PG_BIN/pg_ctl stop -D $PGDATA -m fast" 2>/dev/null || true
    echo "==> Shutdown complete."
    exit 0
}
trap cleanup SIGTERM SIGINT

# ── PostgreSQL ────────────────────────────────────────────────────

# Initialise the data directory if this is the first run
if [ ! -s "$PGDATA/PG_VERSION" ]; then
    echo "==> Initialising PostgreSQL data directory..."
    chown -R postgres:postgres "$PGDATA"
    su -s /bin/bash postgres -c "$PG_BIN/initdb -D $PGDATA"

    # Allow password-based connections on localhost
    echo "host all all 127.0.0.1/32 md5" >> "$PGDATA/pg_hba.conf"
    echo "host all all ::1/128 md5" >> "$PGDATA/pg_hba.conf"
fi

echo "==> Starting PostgreSQL..."
su -s /bin/bash postgres -c "$PG_BIN/pg_ctl -D $PGDATA -l /var/log/postgresql.log start -w"

# Create database and user if they don't exist
PG_USER="${POSTGRES_USER:-luthien}"
PG_DB="${POSTGRES_DB:-luthien_control}"
PG_PASS="${POSTGRES_PASSWORD:-luthien_dev_password}"

su -s /bin/bash postgres -c "psql -tc \"SELECT 1 FROM pg_roles WHERE rolname='$PG_USER'\" | grep -q 1 || psql -c \"CREATE USER $PG_USER WITH PASSWORD '$PG_PASS';\""
su -s /bin/bash postgres -c "psql -tc \"SELECT 1 FROM pg_database WHERE datname='$PG_DB'\" | grep -q 1 || psql -c \"CREATE DATABASE $PG_DB OWNER $PG_USER;\""

echo "==> PostgreSQL is ready."

# ── Redis ─────────────────────────────────────────────────────────
REDIS_DATA="/data"
mkdir -p "$REDIS_DATA"

echo "==> Starting Redis..."
redis-server --daemonize yes \
    --dir "$REDIS_DATA" \
    --save 60 1 \
    --appendonly yes

# Wait for Redis to accept connections
until redis-cli ping > /dev/null 2>&1; do
    sleep 0.2
done
echo "==> Redis is ready."

# ── Migrations ────────────────────────────────────────────────────
echo "==> Running database migrations..."
export PGHOST="127.0.0.1"
export PGPORT="5432"
export PGUSER="$PG_USER"
export PGPASSWORD="$PG_PASS"
export PGDATABASE="$PG_DB"
export MIGRATIONS_DIR="/app/migrations"
/app/docker/run-migrations.sh
echo "==> Migrations complete."

# ── Gateway ───────────────────────────────────────────────────────
# Wire up localhost connection strings (override any external ones)
export DATABASE_URL="postgresql://${PG_USER}:${PG_PASS}@127.0.0.1:5432/${PG_DB}"
export REDIS_URL="redis://127.0.0.1:6379"

# PaaS PORT bridging (same as start-gateway.sh)
export GATEWAY_PORT="${GATEWAY_PORT:-${PORT:-8000}}"

echo "==> Starting Luthien gateway on port ${GATEWAY_PORT}..."
uv run python -m luthien_proxy.main &
GATEWAY_PID=$!
wait $GATEWAY_PID
