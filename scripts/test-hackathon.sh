#!/bin/bash
# Test the hackathon onboarding flow in a fresh Docker container.
#
# Usage:
#   ./scripts/test-hackathon.sh            # rebuild image + run interactive
#   ./scripts/test-hackathon.sh --no-build # skip rebuild, just run
#
# The container gets:
#   - Your ~/.claude mounted (for Claude Code auth)
#   - The current branch pre-cloned at ~/luthien-proxy (skips git clone)
#   - Dependencies pre-installed (skips uv sync)
#   - No pre-existing luthien state
#
# Inside the container, `luthien hackathon` runs automatically.
# The repo is already at ~/luthien-proxy, so clone/deps steps are skipped.

set -euo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || echo "$(dirname "$0")/..")"

IMAGE="luthien-hackathon-test"
DOCKERFILE="dev/test-hackathon.Dockerfile"

if [[ "${1:-}" != "--no-build" ]]; then
    echo "Building image from current branch..."
    docker build -f "$DOCKERFILE" -t "$IMAGE" . 2>&1 | tail -3
    echo ""
fi

echo "Launching fresh container..."
echo "─────────────────────────────────────────"
# Mount ~/.claude/ read-only for config, but ~/.claude.json read-write
# because Claude Code needs to update auth tokens during the session.
MOUNTS=(-v "$HOME/.claude:/root/.claude:ro")
if [[ -f "$HOME/.claude.json" ]]; then
    MOUNTS+=(-v "$HOME/.claude.json:/root/.claude.json")
fi

exec docker run -it --rm \
    "${MOUNTS[@]}" \
    "$IMAGE" \
    bash -c 'luthien hackathon -y; exec bash'
