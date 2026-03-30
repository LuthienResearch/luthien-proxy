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

# --- migrate from pipx -------------------------------------------------
if command_exists pipx && pipx list --short 2>/dev/null | grep -q '^luthien-cli '; then
    echo "Migrating from pipx to uv..."
    pipx uninstall luthien-cli 2>/dev/null || true
fi

# --- luthien-cli -------------------------------------------------------
echo "Installing luthien-cli..."
uv tool install --force --upgrade luthien-cli --from "git+https://github.com/LuthienResearch/luthien-proxy.git#subdirectory=src/luthien_cli"

# --- onboard -----------------------------------------------------------
# Restore stdin from the terminal so interactive prompts work
# even when this script is piped via `curl ... | bash`.
echo ""
luthien onboard </dev/tty
