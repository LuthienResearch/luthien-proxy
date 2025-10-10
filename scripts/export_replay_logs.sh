#!/usr/bin/env bash
set -euo pipefail

# Export replay callback JSONL log from the running LiteLLM proxy container.
#
# Usage:
#   scripts/export_replay_logs.sh [destination-path]
#
# The script locates the first running Docker container whose name contains
# "litellm-proxy" (e.g. the default docker-compose service
# "luthien-proxy-litellm-proxy-1"), copies the replay log from the path used by
# the callback ("/app/dev/replay_logs.jsonl"), and writes it to the requested
# destination. When no destination is provided we place the copy under
# "dev/replay_logs_export-<timestamp>.jsonl" in the local workspace.

find_container() {
  docker ps --filter "name=litellm-proxy" --format '{{.Names}}' | head -n 1
}

main() {
  local container
  container=$(find_container)
  if [[ -z "${container}" ]]; then
    echo "error: no running litellm-proxy container found" >&2
    exit 1
  fi

  local dest
  if [[ $# -ge 1 ]]; then
    dest=$1
  else
    mkdir -p dev
    dest="dev/replay_logs_export-$(date -u +%Y%m%dT%H%M%SZ).jsonl"
  fi

  local container_path="/app/dev/replay_logs.jsonl"
  echo "Copying ${container_path} from ${container} to ${dest}"
  docker cp "${container}:${container_path}" "${dest}"
  echo "Done"
}

main "$@"
