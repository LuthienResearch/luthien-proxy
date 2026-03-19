#!/usr/bin/env bash
# Luthien installer — installs uv (if needed) + luthien-cli, then runs onboard.
# Usage: curl -fsSL https://raw.githubusercontent.com/LuthienResearch/luthien-proxy/main/scripts/install.sh | bash
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"

command_exists() { command -v "$1" >/dev/null 2>&1; }

# --- uv ---------------------------------------------------------------
if ! command_exists uv; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi

# --- luthien-cli -------------------------------------------------------
echo "Installing luthien-cli..."
uv tool install --force luthien-cli

# --- onboard -----------------------------------------------------------
echo ""
luthien onboard
