#!/bin/bash

# ABOUTME: Service monitoring script to check the status of all Luthien Control components
# ABOUTME: Provides real-time status of Docker containers and service health

set -e

echo "📊 Luthien Control Service Monitor"
echo "================================="

# Function to check if a service is healthy
check_service_health() {
    local service_name=$1
    local health_url=$2

    if curl -f -s "$health_url" > /dev/null 2>&1; then
        echo "✅ $service_name"
    else
        echo "❌ $service_name"
    fi
}

# Check Docker containers
echo ""
echo "🐳 Docker Container Status:"
docker-compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"

echo ""
echo "🏥 Service Health Checks:"
check_service_health "Control Plane" "http://localhost:8081/health"
check_service_health "LiteLLM Proxy" "http://localhost:4000/health"

# Check database connectivity
echo ""
echo "💾 Database Status:"
if docker-compose exec -T db pg_isready -U luthien -d luthien_control > /dev/null 2>&1; then
    echo "✅ PostgreSQL"
else
    echo "❌ PostgreSQL"
fi

# Check Redis connectivity
echo ""
echo "📊 Cache Status:"
if docker-compose exec -T redis redis-cli ping > /dev/null 2>&1; then
    echo "✅ Redis"
else
    echo "❌ Redis"
fi

# Check Ollama status
echo ""
echo "🤖 AI Model Status:"
if curl -f -s "http://localhost:11434/api/tags" > /dev/null 2>&1; then
    echo "✅ Ollama"
    echo "Available models:"
    curl -s "http://localhost:11434/api/tags" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    for model in data.get('models', []):
        print(f'  • {model[\"name\"]}')
except:
    print('  Error parsing models')
"
else
    echo "❌ Ollama"
fi

echo ""
echo "📈 Resource Usage:"
docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}" $(docker-compose ps -q)

echo ""
echo "🔄 Recent Logs (last 10 lines):"
echo "Control Plane:"
docker-compose logs --tail=10 control-plane 2>/dev/null | tail -5 || echo "No logs available"

echo ""
echo "LiteLLM Proxy:"
docker-compose logs --tail=10 litellm-proxy 2>/dev/null | tail -5 || echo "No logs available"

echo ""
echo "💡 Commands:"
echo "  • View all logs: docker-compose logs -f"
echo "  • Restart service: docker-compose restart <service>"
echo "  • Check service logs: docker-compose logs <service>"
echo "  • Test proxy: ./scripts/test_proxy.py"
