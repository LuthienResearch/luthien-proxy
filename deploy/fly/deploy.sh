#!/bin/bash
# Fly.io deployment script for Luthien Proxy
# Usage: ./deploy/fly/deploy.sh [app-name]
#
# This script automates the Fly.io deployment process.
# Prerequisites: flyctl installed and authenticated (fly auth login)

set -euo pipefail

APP_NAME="${1:-luthien-proxy-demo}"
REGION="${FLY_REGION:-iad}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Luthien Proxy Fly.io Deployment ==="
echo "App: $APP_NAME"
echo "Region: $REGION"
echo ""

# Check if flyctl is installed
if ! command -v fly &> /dev/null; then
    echo "Error: flyctl is not installed."
    echo "Install with: curl -L https://fly.io/install.sh | sh"
    exit 1
fi

# Check if logged in
if ! fly auth whoami &> /dev/null; then
    echo "Error: Not logged in to Fly.io"
    echo "Run: fly auth login"
    exit 1
fi

# Create or check app
if ! fly apps list | grep -q "$APP_NAME"; then
    echo "Creating Fly app: $APP_NAME"
    fly apps create "$APP_NAME" --machines
else
    echo "App $APP_NAME already exists"
fi

# Check for Postgres
PG_NAME="${APP_NAME}-db"
if ! fly postgres list | grep -q "$PG_NAME"; then
    echo ""
    echo "Creating Postgres cluster: $PG_NAME"
    fly postgres create --name "$PG_NAME" --region "$REGION" --initial-cluster-size 1 --vm-size shared-cpu-1x --volume-size 1

    echo "Attaching Postgres to app..."
    fly postgres attach "$PG_NAME" --app "$APP_NAME"
else
    echo "Postgres $PG_NAME already exists"
fi

# Check for Redis (Upstash)
echo ""
echo "Note: For Redis, create an Upstash Redis instance:"
echo "  fly redis create --name ${APP_NAME}-redis"
echo "  Then set REDIS_URL secret manually"

# Prompt for secrets
echo ""
echo "=== Secret Configuration ==="
echo "The following secrets are required:"
echo "  - PROXY_API_KEY: API key for client authentication"
echo "  - ADMIN_API_KEY: API key for admin endpoints"
echo "  - OPENAI_API_KEY: OpenAI API key (if using OpenAI models)"
echo "  - ANTHROPIC_API_KEY: Anthropic API key (if using Claude models)"
echo ""

read -p "Configure secrets now? (y/n) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Enter PROXY_API_KEY (for client auth):"
    read -s PROXY_API_KEY

    echo "Enter ADMIN_API_KEY (for admin endpoints):"
    read -s ADMIN_API_KEY

    echo "Enter OPENAI_API_KEY (press enter to skip):"
    read -s OPENAI_API_KEY

    echo "Enter ANTHROPIC_API_KEY (press enter to skip):"
    read -s ANTHROPIC_API_KEY

    # Set secrets
    fly secrets set \
        PROXY_API_KEY="$PROXY_API_KEY" \
        ADMIN_API_KEY="$ADMIN_API_KEY" \
        --app "$APP_NAME"

    if [[ -n "$OPENAI_API_KEY" ]]; then
        fly secrets set OPENAI_API_KEY="$OPENAI_API_KEY" --app "$APP_NAME"
    fi

    if [[ -n "$ANTHROPIC_API_KEY" ]]; then
        fly secrets set ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" --app "$APP_NAME"
    fi

    echo "Secrets configured successfully"
fi

# Run migrations
echo ""
echo "=== Database Migrations ==="
echo "Running database migrations..."
fly ssh console --app "$APP_NAME" -C "cd /app && for f in migrations/*.sql; do psql \$DATABASE_URL -f \$f; done" 2>/dev/null || echo "Note: Run migrations after first deploy"

# Deploy
echo ""
echo "=== Deploying ==="
cd "$PROJECT_ROOT"
fly deploy --config deploy/fly/fly.toml --app "$APP_NAME" --region "$REGION"

# Show status
echo ""
echo "=== Deployment Complete ==="
fly status --app "$APP_NAME"
echo ""
echo "App URL: https://${APP_NAME}.fly.dev"
echo ""
echo "Test with:"
echo "  curl https://${APP_NAME}.fly.dev/health"
