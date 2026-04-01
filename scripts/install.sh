#!/usr/bin/env bash
# Luthien installer — installs uv (if needed) + luthien-cli, then runs onboard.
# Usage: curl -fsSL https://raw.githubusercontent.com/LuthienResearch/luthien-proxy/main/scripts/install.sh | bash
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"

command_exists() { command -v "$1" >/dev/null 2>&1; }

# --- git (required for uv to fetch from GitHub) ------------------------
# On macOS, /usr/bin/git is a shim that triggers the Xcode Command Line
# Tools installer.  When stdin is piped (curl | bash) the interactive
# dialog cannot run and `git` silently exits with status 1.  Detect this
# early so users get a clear message instead of a cryptic uv failure.
if ! git --version >/dev/null 2>&1; then
    echo ""
    echo "ERROR: A working 'git' is required but was not found."
    if [ "$(uname -s)" = "Darwin" ]; then
        echo ""
        echo "On macOS, install the Xcode Command Line Tools first:"
        echo ""
        echo "    xcode-select --install"
        echo ""
        echo "After the installation finishes, re-run this script."
    else
        echo "Please install git and re-run this script."
    fi
    exit 1
fi

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
LUTHIEN_REF="${LUTHIEN_REF:-}"
if [ -n "$LUTHIEN_REF" ]; then
    GIT_SOURCE="git+https://github.com/LuthienResearch/luthien-proxy.git@${LUTHIEN_REF}#subdirectory=src/luthien_cli"
else
    GIT_SOURCE="git+https://github.com/LuthienResearch/luthien-proxy.git#subdirectory=src/luthien_cli"
fi
uv tool install --force --upgrade luthien-cli --from "$GIT_SOURCE"

# --- onboard -----------------------------------------------------------
# Restore stdin from the terminal so interactive prompts work
# even when this script is piped via `curl ... | bash`.
echo ""
luthien onboard </dev/tty
