#!/usr/bin/env bash
# Requires: bash 3.2+
# ABOUTME: Build and run wrapper for luthien-sami Docker container
# ABOUTME: Provides commands to build, run, smoke test, and clean the container

set -euo pipefail

IMAGE_NAME="luthien-sami"
IMAGE_TAG="latest"
VOLUME_NAME="luthien-sami-data"
DOCKERFILE="docker/Dockerfile.sami"

_check_docker() {
  docker info >/dev/null 2>&1 || {
    echo "ERROR: Docker daemon not reachable. Is Docker running?" >&2
    exit 1
  }
}

build() {
  _check_docker
  echo "Building ${IMAGE_NAME}:${IMAGE_TAG} from ${DOCKERFILE}..."
  docker build -f "${DOCKERFILE}" -t "${IMAGE_NAME}:${IMAGE_TAG}" .
  echo "Build complete."
}

run() {
  _check_docker
  echo "Starting ${IMAGE_NAME}:${IMAGE_TAG}..."
  echo "Set ANTHROPIC_API_KEY in your environment before running."
  docker run -it --rm \
    -p 8000:8000 \
    -v "${VOLUME_NAME}:/data" \
    -e ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
    "${IMAGE_NAME}:${IMAGE_TAG}"
}

smoke() {
  _check_docker
  echo "Running smoke test..."
  docker run --rm \
    -e ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
    "${IMAGE_NAME}:${IMAGE_TAG}" \
    /work/scripts/track_a_smoke.sh
}

clean() {
  echo "Removing image and volume..."
  docker rmi "${IMAGE_NAME}:${IMAGE_TAG}" 2>/dev/null || true
  docker volume rm "${VOLUME_NAME}" 2>/dev/null || true
  echo "Clean complete."
}

usage() {
  cat <<EOF
Usage: $(basename "$0") <command>

Commands:
  build   Build the luthien-sami Docker image
  run     Run the container interactively (gateway + opencode)
  smoke   Run the track_a_smoke.sh script inside the container
  clean   Remove the image and data volume
  help    Show this help message

Examples:
  $(basename "$0") build
  ANTHROPIC_API_KEY=sk-... $(basename "$0") run
  $(basename "$0") smoke
  $(basename "$0") clean
EOF
}

case "${1:-help}" in
  build) build ;;
  run)   run ;;
  smoke) smoke ;;
  clean) clean ;;
  help)  usage ;;
  *)
    echo "ERROR: Unknown command: ${1}" >&2
    usage >&2
    exit 1
    ;;
esac
