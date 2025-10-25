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
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

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
    if [ -z "$OPENAI_API_KEY" ] || [ "$OPENAI_API_KEY" = "your_openai_api_key_here" ]; then
        echo "ℹ️  INFO: OPENAI_API_KEY not set (only local models will work)"
    fi

    if [ -z "$ANTHROPIC_API_KEY" ] || [ "$ANTHROPIC_API_KEY" = "your_anthropic_api_key_here" ]; then
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

# Apply Prisma migrations for the control plane schema
echo "🗂️  Applying control-plane Prisma migrations..."
docker compose run --rm control-plane-migrations >/dev/null
echo "✅ Prisma migrations applied"

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

# Start single-container local-llm (Ollama with native OpenAI API)
echo "🧰 Starting local-llm (Ollama with built-in OpenAI API)..."
docker compose up -d local-llm

echo "⏳ Waiting for Ollama OpenAI API to be ready..."
ollama_port="${OLLAMA_PORT:-11434}"
timeout=120
while ! curl -sf "http://localhost:${ollama_port}/v1/models" > /dev/null 2>&1; do
    sleep 2
    timeout=$((timeout - 2))
    if [ $timeout -le 0 ]; then
        echo "❌ Ollama OpenAI API failed to start within expected time"
        exit 1
    fi
done
echo "✅ Ollama OpenAI API is ready"

# Start V2 gateway (integrated FastAPI + LiteLLM)
echo "🚀 Starting V2 gateway (integrated proxy)..."
docker compose up -d v2-gateway

# Wait for services to be healthy
echo "⏳ Waiting for services to be healthy..."
services_healthy=true
for service in v2-gateway local-llm; do
    if ! wait_for_service "$service" 60; then
        services_healthy=false
    fi
done

if [ "$services_healthy" = true ]; then
    echo ""
    echo "🎉 Luthien V2 is ready!"
    echo ""
    echo "📋 Service URLs:"
    echo "   • V2 Gateway (OpenAI-compatible): http://localhost:${V2_GATEWAY_PORT:-8000}"
    echo "   • PostgreSQL:     localhost:${POSTGRES_PORT:-5432}"
    echo "   • Redis:          localhost:${REDIS_PORT:-6379}"
    echo "   • Ollama OpenAI API: http://localhost:${ollama_port} (OpenAI-compatible)"
    echo ""
    echo "📊 To view logs:"
    echo "   docker compose logs -f v2-gateway"
    echo ""
    echo "🛑 To stop all services:"
    echo "   docker compose down"
else
    echo ""
    echo "⚠️ Some services may not be healthy. Check logs:"
    echo "   docker compose logs"
    exit 1
fi
