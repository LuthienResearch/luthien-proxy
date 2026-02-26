#!/bin/bash

# ABOUTME: Quick-start script for Luthien standalone single-container development
# ABOUTME: Builds and runs the all-in-one Docker container with auto-port selection

set -e

echo "🚀 Starting Luthien standalone container setup..."

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

# Auto-select free ports for any port variables not pinned in .env
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/find-available-ports.sh"

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

# Stop any existing standalone container
echo "🛑 Stopping any existing standalone container..."
docker stop luthien-standalone-dev 2>/dev/null || true
docker rm luthien-standalone-dev 2>/dev/null || true

# Build the standalone container
echo "🔨 Building standalone container..."
docker build -f docker/Dockerfile.standalone -t luthien-standalone-dev .

# Create named volumes if they don't exist
echo "📦 Creating persistent volumes..."
docker volume create luthien-standalone-pgdata 2>/dev/null || true
docker volume create luthien-standalone-redis 2>/dev/null || true

# Start the container
echo "🐳 Starting standalone container..."
docker run -d \
    --name luthien-standalone-dev \
    -p "${GATEWAY_PORT:-8000}:${GATEWAY_PORT:-8000}" \
    -v luthien-standalone-pgdata:/var/lib/postgresql/data \
    -v luthien-standalone-redis:/data \
    --env-file .env \
    luthien-standalone-dev

# Wait for the gateway to be ready
echo "⏳ Waiting for gateway to be ready..."
timeout=60
gateway_port="${GATEWAY_PORT:-8000}"

while [ $timeout -gt 0 ]; do
    if curl -f "http://localhost:${gateway_port}/health" > /dev/null 2>&1; then
        echo "✅ Gateway is healthy"
        break
    fi
    sleep 2
    timeout=$((timeout - 2))
    if [ $timeout -le 0 ]; then
        echo "❌ Gateway failed to start within 60 seconds"
        echo "📋 Check logs with: docker logs luthien-standalone-dev"
        exit 1
    fi
done

echo ""
echo "🎉 Luthien standalone container is ready!"
echo ""
echo "📋 Service URLs:"
echo "   • Gateway (OpenAI-compatible): http://localhost:${gateway_port}"
echo ""
echo "📊 To view logs:"
echo "   docker logs -f luthien-standalone-dev"
echo ""
echo "🛑 To stop the container:"
echo "   docker stop luthien-standalone-dev"
echo ""
echo "🗑️  To remove container and volumes:"
echo "   docker stop luthien-standalone-dev"
echo "   docker rm luthien-standalone-dev"
echo "   docker volume rm luthien-standalone-pgdata luthien-standalone-redis"
echo ""
echo "⚠️  DOGFOODING NOTE: If running Claude Code through this proxy,"
echo "   do NOT restart/stop Docker from the proxied session — it will"
echo "   kill the proxy and sever the agent's API connection."
echo "   Use a separate terminal for Docker commands."