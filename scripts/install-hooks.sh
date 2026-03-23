#!/usr/bin/env bash
# Install git hooks from scripts/ into .git/hooks/
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOKS_DIR="$(git rev-parse --git-common-dir)/hooks"

ln -sf "$REPO_ROOT/scripts/pre-commit" "$HOOKS_DIR/pre-commit"
echo "Installed pre-commit hook."
