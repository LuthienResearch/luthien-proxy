#!/bin/bash
# VPS Deployment Script for Luthien Proxy
#
# Usage: ./deploy/docker/deploy-vps.sh [domain]
#
# This script sets up Luthien Proxy on a fresh VPS with:
# - Docker and Docker Compose
# - PostgreSQL, Redis, and the Gateway
# - Caddy reverse proxy with automatic HTTPS
#
# Tested on: Ubuntu 22.04+, Debian 11+

set -euo pipefail

DOMAIN="${1:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "========================================"
echo "  Luthien Proxy VPS Deployment"
echo "========================================"
echo ""

# Check if running as root or with sudo
if [[ $EUID -ne 0 ]]; then
    SUDO="sudo"
else
    SUDO=""
fi

# Detect OS
if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    OS=$ID
else
    echo "Error: Cannot detect OS"
    exit 1
fi

echo "Detected OS: $OS"

# Install Docker if not present
if ! command -v docker &> /dev/null; then
    echo ""
    echo "Installing Docker..."
    case $OS in
        ubuntu|debian)
            $SUDO apt-get update
            $SUDO apt-get install -y ca-certificates curl gnupg
            $SUDO install -m 0755 -d /etc/apt/keyrings
            curl -fsSL https://download.docker.com/linux/$OS/gpg | $SUDO gpg --dearmor -o /etc/apt/keyrings/docker.gpg
            $SUDO chmod a+r /etc/apt/keyrings/docker.gpg
            echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$OS $(lsb_release -cs) stable" | $SUDO tee /etc/apt/sources.list.d/docker.list > /dev/null
            $SUDO apt-get update
            $SUDO apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            ;;
        centos|rhel|fedora)
            $SUDO dnf install -y dnf-plugins-core
            $SUDO dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
            $SUDO dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
            $SUDO systemctl start docker
            $SUDO systemctl enable docker
            ;;
        *)
            echo "Unsupported OS: $OS"
            echo "Please install Docker manually: https://docs.docker.com/engine/install/"
            exit 1
            ;;
    esac
    echo "Docker installed successfully"
fi

# Add current user to docker group
if ! groups | grep -q docker; then
    $SUDO usermod -aG docker $USER
    echo "Added $USER to docker group. You may need to log out and back in."
fi

# Navigate to project root
cd "$PROJECT_ROOT"

# Setup environment file
if [[ ! -f deploy/docker/.env ]]; then
    echo ""
    echo "Setting up environment configuration..."
    cp deploy/docker/.env.prod.example deploy/docker/.env

    # Generate secure passwords
    POSTGRES_PASSWORD=$(openssl rand -base64 24 | tr -d '=+/')
    PROXY_API_KEY=$(openssl rand -hex 32)
    ADMIN_API_KEY=$(openssl rand -hex 32)

    # Update .env file
    sed -i "s/^POSTGRES_PASSWORD=$/POSTGRES_PASSWORD=$POSTGRES_PASSWORD/" deploy/docker/.env
    sed -i "s/^PROXY_API_KEY=$/PROXY_API_KEY=$PROXY_API_KEY/" deploy/docker/.env
    sed -i "s/^ADMIN_API_KEY=$/ADMIN_API_KEY=$ADMIN_API_KEY/" deploy/docker/.env

    if [[ -n "$DOMAIN" ]]; then
        sed -i "s/^DOMAIN_NAME=.*/DOMAIN_NAME=$DOMAIN/" deploy/docker/.env
    fi

    echo ""
    echo "Generated secure credentials:"
    echo "  PROXY_API_KEY: $PROXY_API_KEY"
    echo "  ADMIN_API_KEY: $ADMIN_API_KEY"
    echo ""
    echo "IMPORTANT: Save these keys securely! They are stored in deploy/docker/.env"
    echo ""

    # Prompt for LLM API keys
    echo "Enter your LLM provider API keys (press Enter to skip):"
    read -p "OPENAI_API_KEY: " OPENAI_KEY
    read -p "ANTHROPIC_API_KEY: " ANTHROPIC_KEY

    if [[ -n "$OPENAI_KEY" ]]; then
        sed -i "s/^OPENAI_API_KEY=$/OPENAI_API_KEY=$OPENAI_KEY/" deploy/docker/.env
    fi
    if [[ -n "$ANTHROPIC_KEY" ]]; then
        sed -i "s/^ANTHROPIC_API_KEY=$/ANTHROPIC_API_KEY=$ANTHROPIC_KEY/" deploy/docker/.env
    fi
else
    echo "Using existing deploy/docker/.env configuration"
fi

# Deploy
echo ""
echo "Starting deployment..."
cd deploy/docker
docker compose -f docker-compose.prod.yaml up -d --build

# Wait for services
echo ""
echo "Waiting for services to start..."
sleep 10

# Check health
echo ""
echo "Checking service health..."
if docker compose -f docker-compose.prod.yaml ps | grep -q "healthy"; then
    echo "Services are healthy!"
else
    echo "Warning: Some services may still be starting. Check with:"
    echo "  docker compose -f deploy/docker/docker-compose.prod.yaml ps"
fi

# Output info
echo ""
echo "========================================"
echo "  Deployment Complete!"
echo "========================================"
echo ""

DOMAIN_NAME=$(grep "^DOMAIN_NAME=" .env | cut -d'=' -f2)
PROXY_KEY=$(grep "^PROXY_API_KEY=" .env | cut -d'=' -f2)

if [[ "$DOMAIN_NAME" != "your.domain.com" && "$DOMAIN_NAME" != "localhost" ]]; then
    echo "Your Luthien Proxy is available at:"
    echo "  https://$DOMAIN_NAME"
else
    echo "Your Luthien Proxy is running locally."
    echo "Configure DOMAIN_NAME in deploy/docker/.env for HTTPS."
fi

echo ""
echo "Test the deployment:"
echo "  curl https://$DOMAIN_NAME/health"
echo ""
echo "Use the API:"
echo "  curl https://$DOMAIN_NAME/v1/chat/completions \\"
echo "    -H 'Authorization: Bearer $PROXY_KEY' \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"model\": \"gpt-4o-mini\", \"messages\": [{\"role\": \"user\", \"content\": \"Hello!\"}]}'"
echo ""
echo "Manage deployment:"
echo "  cd deploy/docker"
echo "  docker compose -f docker-compose.prod.yaml logs -f     # View logs"
echo "  docker compose -f docker-compose.prod.yaml restart     # Restart"
echo "  docker compose -f docker-compose.prod.yaml down        # Stop"
