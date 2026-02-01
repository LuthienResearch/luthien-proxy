#!/usr/bin/env bash
set -euo pipefail

# Capture raw SSE stream from gateway for a simple prompt.
# Writes to /tmp/gateway_sse_capture.txt by default.

OUT_FILE=${1:-/tmp/gateway_sse_capture.txt}

# Load .env if present
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

API_KEY=${PROXY_API_KEY:-}
if [ -z "$API_KEY" ]; then
  echo "PROXY_API_KEY not set in environment or .env" >&2
  exit 1
fi

payload='{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hello"}],"stream":true}'

curl -sS -N \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d "$payload" \
  http://localhost:8000/v1/chat/completions \
  | sed -n '1,200p' \
  > "$OUT_FILE"

echo "Wrote SSE capture to $OUT_FILE"
