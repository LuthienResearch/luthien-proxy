#!/usr/bin/env bash
set -euo pipefail

MODEL_NAME=${MODEL_NAME:-gemma2:2b}

echo "[local-llm] Starting Ollama server..."
ollama serve &

# Wait for Ollama
for i in {1..60}; do
  if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "[local-llm] Ollama is up"
    break
  fi
  sleep 1
done

if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "[local-llm] ERROR: Ollama failed to start" >&2
  exit 1
fi

echo "[local-llm] Pulling model: ${MODEL_NAME}"
ollama pull "${MODEL_NAME}" || true

echo "[local-llm] Starting LiteLLM gateway..."
exec uv run litellm --config /app/config/local_llm_config.yaml --port 4000 --host 0.0.0.0
