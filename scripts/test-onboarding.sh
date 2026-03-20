#!/bin/bash
# Test the onboarding flow in a fresh Docker container.
#
# Usage:
#   ./dev/test-onboarding.sh          # rebuild image + run interactive
#   ./dev/test-onboarding.sh --no-build  # skip rebuild, just run
#
# The container gets:
#   - Your ~/.claude mounted (for Claude Code auth)
#   - A fresh luthien install from the current branch
#   - No pre-existing luthien state
#
# Inside the container, `luthien onboard` runs automatically.
# Press Enter to launch Claude Code, or q to quit.

set -euo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || echo "$(dirname "$0")/..")"

IMAGE="luthien-onboard-test"
DOCKERFILE="dev/test-onboarding.Dockerfile"

if [[ "${1:-}" != "--no-build" ]]; then
    echo "Building image from current branch..."
    docker build -f "$DOCKERFILE" -t "$IMAGE" . 2>&1 | tail -3
    echo ""
fi

echo "Launching fresh container..."
echo "─────────────────────────────────────────"
exec docker run -it --rm \
    -v "$HOME/.claude:/root/.claude:ro" \
    "$IMAGE" \
    bash -c 'luthien onboard; exec bash'
