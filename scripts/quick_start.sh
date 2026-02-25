#!/bin/bash
# Requires: bash 3.2+
# ABOUTME: Minimal quick-start script for Luthien Control development environment
# ABOUTME: Validates dependencies, sets up environment, and starts core services

set -e

echo "🚀 Starting Luthien Control quick setup..."

# Check required dependencies
echo "🔍 Checking dependencies..."

if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first."
    exit 1
fi

if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker and try again."
    exit 1
fi

if ! command -v uv &> /dev/null; then
    echo "❌ uv is not installed. Please install uv first: https://docs.astral.sh/uv/"
    exit 1
fi

echo "✅ Dependencies check passed"

wait_for_service() {
    local service="$1"
    local timeout="$2"
    local elapsed=0
    local interval=2

    while [ "$elapsed" -lt "$timeout" ]; do
        if docker compose ps "$service" | tail -n +2 | grep -q "Up"; then
            echo "✅ $service is healthy"
            return 0
        fi
        sleep "$interval"
        elapsed=$((elapsed + interval))
    done

    echo "⚠️ $service did not become healthy within ${timeout}s"
    return 1
}

# Create .env from template if missing
if [ ! -f .env ]; then
    echo "📝 Creating .env file from template..."
    if [ ! -f .env.example ]; then
        echo "❌ .env.example not found. Please ensure it exists."
        exit 1
    fi
    cp .env.example .env
    echo "✅ Created .env file. Edit it with your API keys if needed."
fi

# Source environment variables
if [[ -f .env ]]; then
    set -a
    # shellcheck source=/dev/null
    source .env
    set +a
fi

# Auto-select free ports for any port variables not pinned in .env
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=find-available-ports.sh
source "${SCRIPT_DIR}/find-available-ports.sh"

# Derive project name from worktree directory to avoid collisions between worktrees
if [[ -z "${COMPOSE_PROJECT_NAME:-}" ]]; then
    worktree_dir="$(basename "$(pwd)")"
    export COMPOSE_PROJECT_NAME="luthien-${worktree_dir}"
fi
echo "📦 Docker project: ${COMPOSE_PROJECT_NAME}"

# Check for insecure default credentials
echo "🔒 Checking for insecure default credentials..."
insecure_defaults=false

if [ -f .env ] && [ -f .env.example ]; then
    # Check POSTGRES_PASSWORD
    env_postgres_pw=$(grep "^POSTGRES_PASSWORD=" .env 2>/dev/null | cut -d'=' -f2)
    example_postgres_pw=$(grep "^POSTGRES_PASSWORD=" .env.example 2>/dev/null | cut -d'=' -f2)
    if [ -n "$env_postgres_pw" ] && [ "$env_postgres_pw" = "$example_postgres_pw" ]; then
        echo "⚠️  WARNING: POSTGRES_PASSWORD is using the default dev value!"
        echo "   This is INSECURE for production. Change it in .env"
        insecure_defaults=true
    fi

    # Check PROXY_API_KEY
    env_api_key=$(grep "^PROXY_API_KEY=" .env 2>/dev/null | cut -d'=' -f2)
    example_api_key=$(grep "^PROXY_API_KEY=" .env.example 2>/dev/null | cut -d'=' -f2)
    if [ -n "$env_api_key" ] && [ "$env_api_key" = "$example_api_key" ]; then
        echo "⚠️  WARNING: PROXY_API_KEY is using the default dev value!"
        echo "   This is INSECURE for production. Change it in .env"
        insecure_defaults=true
    fi

    # Check if real API keys are missing (empty or placeholder)
    if [[ -z "${OPENAI_API_KEY:-}" ]] || [[ "$OPENAI_API_KEY" = "your_openai_api_key_here" ]]; then
        echo "ℹ️  INFO: OPENAI_API_KEY not set (only local models will work)"
    fi

    if [[ -z "${ANTHROPIC_API_KEY:-}" ]] || [[ "$ANTHROPIC_API_KEY" = "your_anthropic_api_key_here" ]]; then
        echo "ℹ️  INFO: ANTHROPIC_API_KEY not set (only local models will work)"
    fi
fi

if [ "$insecure_defaults" = true ]; then
    echo ""
    echo "⚠️  SECURITY WARNING: You are using default development credentials!"
    echo "   This is OK for local development, but NEVER use these in production."
    echo "   Update .env with secure values before deploying."
    echo ""
fi

# Install Python dependencies
echo "📦 Installing Python dependencies..."
uv sync --dev

# Stop any existing services
echo "🛑 Stopping any existing services..."
docker compose down --remove-orphans

# Also clean up containers from the default project name (directory-based).
# quick_start.sh sets COMPOSE_PROJECT_NAME=luthien-<dir>, but running
# `docker compose up` directly uses just the directory name. Those orphaned
# containers can hold ports and cause bind failures on the next start.
default_project="$(basename "$(pwd)")"
if [ "$default_project" != "$COMPOSE_PROJECT_NAME" ]; then
    if docker compose -p "$default_project" ps -q 2>/dev/null | grep -q .; then
        echo "🧹 Cleaning up orphaned containers from project '$default_project'..."
        docker compose -p "$default_project" down --remove-orphans
    fi
fi

# Start core services
echo "🐳 Starting core services..."
docker compose up -d db redis

# Wait for database to be ready
echo "⏳ Waiting for PostgreSQL to be ready..."
timeout=30
while ! docker compose exec -T db pg_isready -U "${POSTGRES_USER:-luthien}" -d "${POSTGRES_DB:-luthien_control}" > /dev/null 2>&1; do
    sleep 1
    timeout=$((timeout - 1))
    if [[ "$timeout" -eq 0 ]]; then
        echo "❌ PostgreSQL failed to start within 30 seconds"
        exit 1
    fi
done
echo "✅ PostgreSQL is ready"

# Wait for Redis
echo "⏳ Waiting for Redis to be ready..."
timeout=10
while ! docker compose exec -T redis redis-cli ping > /dev/null 2>&1; do
    sleep 1
    timeout=$((timeout - 1))
    if [[ "$timeout" -eq 0 ]]; then
        echo "❌ Redis failed to start within 10 seconds"
        exit 1
    fi
done
echo "✅ Redis is ready"

# Start gateway (integrated FastAPI + LiteLLM)
echo "🚀 Starting gateway (integrated proxy)..."
docker compose up -d gateway

# Wait for services to be healthy
echo "⏳ Waiting for services to be healthy..."
services_healthy=true
for service in gateway; do
    if ! wait_for_service "$service" 60; then
        services_healthy=false
    fi
done


# Telemetry opt-in/out prompt (only when no env var and no DB value)
if [ "$services_healthy" = true ] && [ -z "${USAGE_TELEMETRY:-}" ]; then
    gateway_url="http://localhost:${GATEWAY_PORT:-8000}"
    admin_key="${ADMIN_API_KEY:-}"

    if [ -n "$admin_key" ]; then
        telemetry_resp=$(curl -s -H "Authorization: Bearer $admin_key" "${gateway_url}/api/admin/telemetry" 2>/dev/null || echo "")

        # Only prompt if no one has made an explicit choice yet
        needs_prompt=$(echo "$telemetry_resp" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    # Skip if env var overrides or user already configured via API/prompt
    print('no' if d.get('env_override') or d.get('user_configured') else 'yes')
except Exception:
    print('no')
" 2>/dev/null || echo "no")

        if [ "$needs_prompt" = "yes" ]; then
            echo ""
            echo "📊 Anonymous usage telemetry helps the Luthien team understand"
            echo "   how the proxy is being used (aggregate counts only, no"
            echo "   identifying data)."
            echo ""
            read -r -p "   Send anonymous usage data? [Y/n] " telemetry_choice </dev/tty 2>/dev/null || telemetry_choice="Y"
            case "$telemetry_choice" in
                [nN]*)
                    curl -s -X PUT -H "Authorization: Bearer $admin_key" \
                        -H "Content-Type: application/json" \
                        -d '{"enabled": false}' \
                        "${gateway_url}/api/admin/telemetry" > /dev/null 2>&1
                    echo "   ✅ Telemetry disabled. Change anytime via USAGE_TELEMETRY env var or admin API."
                    ;;
                *)
                    curl -s -X PUT -H "Authorization: Bearer $admin_key" \
                        -H "Content-Type: application/json" \
                        -d '{"enabled": true}' \
                        "${gateway_url}/api/admin/telemetry" > /dev/null 2>&1
                    echo "   ✅ Telemetry enabled. Change anytime via USAGE_TELEMETRY env var or admin API."
                    ;;
            esac
        fi
    fi
fi

if [ "$services_healthy" = true ]; then
    echo ""
    echo "🎉 Luthien is ready!"
    echo ""
    echo "📋 Service URLs:"
    echo "   • Gateway (OpenAI-compatible): http://localhost:${GATEWAY_PORT:-8000}"
    echo "   • PostgreSQL:     localhost:${POSTGRES_PORT:-5432}"
    echo "   • Redis:          localhost:${REDIS_PORT:-6379}"
    echo ""
    echo "📊 To view logs:"
    echo "   docker compose logs -f gateway"
    echo ""
    echo "🛑 To stop all services:"
    echo "   docker compose down"
    echo ""
    echo "⚠️  DOGFOODING NOTE: If running Claude Code through this proxy,"
    echo "   do NOT restart/stop Docker from the proxied session — it will"
    echo "   kill the proxy and sever the agent's API connection."
    echo "   Use a separate terminal for Docker commands."
else
    echo ""
    echo "⚠️ Some services may not be healthy. Check logs:"
    echo "   docker compose logs"
    exit 1
fi
