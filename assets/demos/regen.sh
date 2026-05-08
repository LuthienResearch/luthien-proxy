#!/usr/bin/env bash
# Regenerate raw VHS clips.  Run from anywhere; resolves repo root via git.
#
# Usage:
#   assets/demos/regen.sh              # render all tapes
#   assets/demos/regen.sh install      # render one tape (matches *install*.tape)
#
# Each tape's pre-conditions live in its header comment.  This script does
# NOT reset state between tapes — that's the operator's job.  Read the tape
# headers before recording.

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
TAPES_DIR="$ROOT/assets/demos"
RAW_DIR="$TAPES_DIR/raw"

mkdir -p "$RAW_DIR"

if ! command -v vhs >/dev/null 2>&1; then
    echo "vhs not found.  Install:  brew install vhs ffmpeg" >&2
    exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "ffmpeg not found (vhs needs it for MP4 output).  Install:  brew install ffmpeg" >&2
    exit 1
fi

filter="${1:-}"

mapfile -t tapes < <(find "$TAPES_DIR" -maxdepth 1 -name "*.tape" | sort)
if [[ -n "$filter" ]]; then
    mapfile -t tapes < <(printf '%s\n' "${tapes[@]}" | grep -- "$filter" || true)
fi

if [[ ${#tapes[@]} -eq 0 ]]; then
    echo "No tapes matched filter '$filter'." >&2
    exit 1
fi

for tape in "${tapes[@]}"; do
    name="$(basename "$tape" .tape)"
    echo
    echo "=== Rendering $name ==="
    echo "Pre-conditions are in $tape — confirm before each run."
    vhs "$tape"
done

echo
echo "Done.  Raw clips:  $RAW_DIR"
ls -lh "$RAW_DIR"/*.mp4 2>/dev/null || true
