#!/bin/bash

# ABOUTME: Development environment setup script for Luthien Control
# ABOUTME: Initializes the development environment with Docker containers and basic configuration

set -e

echo "🔧 Setting up Luthien Control development environment..."

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker and try again."
    exit 1
fi

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "❌ uv is not installed. Please install uv first: https://docs.astral.sh/uv/"
    exit 1
fi

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo "📝 Creating .env file from template..."
    cp .env.example .env
    echo "✅ Created .env file. Please edit it with your API keys if needed."
fi

# Install Python dependencies
echo "📦 Installing Python dependencies..."
uv sync --dev

# Build Docker images
echo "🐳 Building Docker images..."
docker compose build

# Start services
echo "🚀 Starting services..."
docker compose up -d db redis

# Wait for database to be ready
echo "⏳ Waiting for database to be ready..."
timeout=30
while ! docker compose exec -T db pg_isready -U luthien -d luthien_control > /dev/null 2>&1; do
    sleep 1
    timeout=$((timeout - 1))
    if [ $timeout -eq 0 ]; then
        echo "❌ Database failed to start within 30 seconds"
        exit 1
    fi
done

echo "✅ Database is ready"

# Start Ollama and pull default model
# echo "🤖 Starting Ollama and pulling default model..."
# docker compose up -d ollama

# # Wait for Ollama to be ready
# echo "⏳ Waiting for Ollama to be ready..."
# timeout=60
# while ! docker compose exec -T ollama curl -f http://localhost:11434/api/tags > /dev/null 2>&1; do
    # sleep 2
    # timeout=$((timeout - 2))
    # if [ $timeout -eq 0 ]; then
        # echo "⚠️  Ollama failed to start within 60 seconds, skipping model pull"
        # break
    # fi
# done

# if [ $timeout -gt 0 ]; then
    # echo "📥 Pulling llama3.1:8b model for trusted monitoring..."
    # docker compose exec ollama ollama pull llama3.1:8b || echo "⚠️  Failed to pull model, continuing anyway"
# fi

# Start control plane and LiteLLM proxy
echo "🎛️  Starting control plane..."
docker compose up -d control-plane

echo "🔄 Starting LiteLLM proxy..."
docker compose up -d litellm-proxy

# Wait for services to be healthy
echo "⏳ Waiting for all services to be healthy..."
sleep 10

# Check service health
services=("db" "redis" "control-plane" "litellm-proxy")
all_healthy=true

for service in "${services[@]}"; do
    if docker compose ps "$service" | grep -q "healthy\|Up"; then
        echo "✅ $service is running"
    else
        echo "❌ $service is not healthy"
        all_healthy=false
    fi
done

if [ "$all_healthy" = true ]; then
    echo ""
    echo "🎉 Development environment is ready!"
    echo ""
    echo "📋 Service URLs:"
    echo "   • LiteLLM Proxy: http://localhost:4000"
    echo "   • Control Plane: http://localhost:8081"
    echo "   • PostgreSQL: localhost:5432"
    echo "   • Redis: localhost:6379"
    # echo "   • Ollama: http://localhost:11434"
    echo ""
    echo "🧪 To test the setup, run:"
    echo "   ./scripts/test_proxy.py"
    echo ""
    echo "📊 To view logs:"
    echo "   docker compose logs -f"
    echo ""
    echo "🛑 To stop all services:"
    echo "   docker compose down"
else
    echo ""
    echo "⚠️  Some services are not healthy. Check logs with:"
    echo "   docker compose logs"
    exit 1
fi
