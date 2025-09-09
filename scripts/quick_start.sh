#!/bin/bash

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
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Install Python dependencies
echo "📦 Installing Python dependencies..."
uv sync --dev

# Stop any existing services
echo "🛑 Stopping any existing services..."
docker compose down --remove-orphans

# Start core services
echo "🐳 Starting core services..."
docker compose up -d db redis

# Wait for database to be ready
echo "⏳ Waiting for PostgreSQL to be ready..."
timeout=30
while ! docker compose exec -T db pg_isready -U "${POSTGRES_USER:-luthien}" -d "${POSTGRES_DB:-luthien_control}" > /dev/null 2>&1; do
    sleep 1
    timeout=$((timeout - 1))
    if [ $timeout -eq 0 ]; then
        echo "❌ PostgreSQL failed to start within 30 seconds"
        exit 1
    fi
done
echo "✅ PostgreSQL is ready"

# Apply idempotent SQL migrations (for new tables like debug_logs)
echo "🗂️  Applying SQL migrations..."
for sql in $(ls -1 migrations/*.sql | sort); do
  echo "   • $sql"
  docker compose exec -T db psql -U "${POSTGRES_USER:-luthien}" -d "${POSTGRES_DB:-luthien_control}" -f "/docker-entrypoint-initdb.d/$(basename "$sql")" >/dev/null 2>&1 || true
done
echo "✅ SQL migrations applied (idempotent)"

# Wait for Redis
echo "⏳ Waiting for Redis to be ready..."
timeout=10
while ! docker compose exec -T redis redis-cli ping > /dev/null 2>&1; do
    sleep 1
    timeout=$((timeout - 1))
    if [ $timeout -eq 0 ]; then
        echo "❌ Redis failed to start within 10 seconds"
        exit 1
    fi
done
echo "✅ Redis is ready"

# Start single-container local-llm (Ollama + LiteLLM)
echo "🧰 Starting local-llm (Ollama + OpenAI-compatible gateway)..."
docker compose up -d local-llm

echo "⏳ Waiting for local-llm to be ready..."
timeout=120
while ! curl -sf "http://localhost:${LOCAL_LLM_PORT:-4010}/test" > /dev/null 2>&1; do
    sleep 2
    timeout=$((timeout - 2))
    if [ $timeout -le 0 ]; then
        echo "❌ local-llm failed to start within expected time"
        exit 1
    fi
done
echo "✅ local-llm is ready"

# Start application services depending on DB/Redis and local LLM
echo "🎛️ Starting control plane..."
docker compose up -d control-plane

echo "🔄 Starting LiteLLM proxy..."
docker compose up -d litellm-proxy

# Wait for services to be healthy
echo "⏳ Waiting for services to be healthy..."
sleep 5

# Check service health
services_healthy=true

for service in control-plane litellm-proxy local-llm; do
    if ! docker compose ps "$service" | grep -q "Up"; then
        echo "⚠️ $service is not running properly"
        services_healthy=false
    fi
done

if [ "$services_healthy" = true ]; then
    echo ""
    echo "🎉 Luthien Control is ready!"
    echo ""
    echo "📋 Service URLs:"
    echo "   • LiteLLM Proxy:  http://localhost:${LITELLM_PORT:-4000}"
    echo "   • Control Plane:  http://localhost:${CONTROL_PLANE_PORT:-8081}"
    echo "   • PostgreSQL:     localhost:${POSTGRES_PORT:-5432}"
    echo "   • Redis:          localhost:${REDIS_PORT:-6379}"
    echo "   • local-llm:      http://localhost:${LOCAL_LLM_PORT:-4010} (OpenAI-compatible)"
    echo "   • Ollama API:     http://localhost:11434 (inside local-llm)"
    echo ""
    echo "📊 To view logs:"
    echo "   docker compose logs -f"
    echo ""
    echo "🛑 To stop all services:"
    echo "   docker compose down"
else
    echo ""
    echo "⚠️ Some services may not be healthy. Check logs:"
    echo "   docker compose logs"
    exit 1
fi
