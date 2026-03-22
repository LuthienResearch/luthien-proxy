#!/usr/bin/env bash
# Luthien hackathon installer — installs uv + gh (if needed) + luthien-cli, then runs hackathon.
# Usage: curl -fsSL https://raw.githubusercontent.com/LuthienResearch/luthien-proxy/main/scripts/install-hackathon.sh | bash
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"

command_exists() { command -v "$1" >/dev/null 2>&1; }

# --- uv ---------------------------------------------------------------
if ! command_exists uv; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi

# --- gh (optional, for fork workflow) ----------------------------------
if ! command_exists gh; then
    echo ""
    echo "Tip: install the GitHub CLI (gh) for automatic fork creation:"
    echo "  https://cli.github.com/"
    echo "Continuing without it — will use git clone instead."
    echo ""
fi

# --- luthien-cli -------------------------------------------------------
echo "Installing luthien-cli..."
uv tool install --force luthien-cli --from "git+https://github.com/LuthienResearch/luthien-proxy.git#subdirectory=src/luthien_cli"

# --- hackathon ---------------------------------------------------------
echo ""
luthien hackathon
