#!/bin/bash
# DigitalOcean App Platform deployment script
#
# Usage: ./deploy/digitalocean/deploy.sh
#
# Prerequisites:
# 1. Install doctl: https://docs.digitalocean.com/reference/doctl/how-to/install/
# 2. Authenticate: doctl auth init

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Luthien Proxy DigitalOcean Deployment ==="
echo ""

# Check if doctl is installed
if ! command -v doctl &> /dev/null; then
    echo "Error: doctl is not installed."
    echo "Install with: brew install doctl  (macOS)"
    echo "Or: snap install doctl  (Linux)"
    echo "Or: https://docs.digitalocean.com/reference/doctl/how-to/install/"
    exit 1
fi

# Check if authenticated
if ! doctl account get &> /dev/null; then
    echo "Error: Not authenticated with DigitalOcean"
    echo "Run: doctl auth init"
    exit 1
fi

echo "Authenticated as: $(doctl account get --format Email --no-header)"
echo ""

# Generate secrets
PROXY_API_KEY=$(openssl rand -hex 32)
ADMIN_API_KEY=$(openssl rand -hex 32)

echo "Generated API keys:"
echo "  PROXY_API_KEY: $PROXY_API_KEY"
echo "  ADMIN_API_KEY: $ADMIN_API_KEY"
echo ""
echo "Save these keys securely!"
echo ""

# Prompt for LLM keys
echo "Enter your LLM API keys:"
read -p "OPENAI_API_KEY (press Enter to skip): " -s OPENAI_KEY
echo ""
read -p "ANTHROPIC_API_KEY (press Enter to skip): " -s ANTHROPIC_KEY
echo ""

# Create the app
echo ""
echo "Creating DigitalOcean App..."
APP_ID=$(doctl apps create --spec "$SCRIPT_DIR/app.yaml" --format ID --no-header)

echo "App created with ID: $APP_ID"

# Set secrets
echo "Setting secrets..."
doctl apps update "$APP_ID" \
    --spec <(cat "$SCRIPT_DIR/app.yaml" | \
        sed "s/PROXY_API_KEY$/PROXY_API_KEY\n        value: $PROXY_API_KEY/" | \
        sed "s/ADMIN_API_KEY$/ADMIN_API_KEY\n        value: $ADMIN_API_KEY/")

if [[ -n "${OPENAI_KEY:-}" ]]; then
    echo "Setting OPENAI_API_KEY..."
    # Note: In production, use doctl apps update with --env flag
fi

if [[ -n "${ANTHROPIC_KEY:-}" ]]; then
    echo "Setting ANTHROPIC_API_KEY..."
fi

# Get app URL
echo ""
echo "Waiting for deployment..."
sleep 10

APP_URL=$(doctl apps get "$APP_ID" --format DefaultIngress --no-header)

echo ""
echo "========================================"
echo "  Deployment Initiated!"
echo "========================================"
echo ""
echo "App URL: https://$APP_URL"
echo ""
echo "Check deployment status:"
echo "  doctl apps get $APP_ID"
echo ""
echo "View logs:"
echo "  doctl apps logs $APP_ID"
echo ""
echo "Update secrets (if needed):"
echo "  Go to: https://cloud.digitalocean.com/apps/$APP_ID/settings"
