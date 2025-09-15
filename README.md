# Luthien Control

Redwood-style AI Control as an LLM proxy for production agentic deployments.

## Quick Start

```bash
# 1. Start everything
./scripts/quick_start.sh

# 2. Test it works
uv run python scripts/test_proxy.py
```

You now have:
- **LiteLLM Proxy** at http://localhost:4000
- **Control Plane** at http://localhost:8081
- **PostgreSQL** and **Redis** fully configured

## Prerequisites

- Docker Desktop (or Docker Engine + Compose)
- Python 3.13+
- [uv](https://docs.astral.sh/uv/)

## Development

```bash
# After code changes, restart services
docker compose restart control-plane    # Control plane only
docker compose restart litellm-proxy    # LiteLLM proxy only

# Run tests
uv run pytest

# Format and lint
uv run ruff format
uv run ruff check --fix
```

## Configuration

Copy `.env.example` to `.env` and add your API keys.

## Architecture

- **LiteLLM Proxy**: OpenAI-compatible gateway with custom hooks
- **Control Plane**: Policy orchestration and decision logic
- **Monitors**: Trusted and untrusted model supervision
- **Policy Engine**: Configuration and threshold management

## Control Policies

We keep the policy surface identical to LiteLLM's hook API and make the proxy hooks dead-simple HTTP wrappers. All logic lives in the Control Plane.

- Proxy hooks → Control Plane:
  - `/hooks/pre` mirrors `async_pre_call_hook(...)`
  - `/hooks/post_success` mirrors `async_post_call_success_hook(...)`
  - Streaming is chunk-by-chunk:
    - Streaming chunks are forwarded via generic hook(s) (e.g., `async_post_call_streaming_iterator_hook`); the control plane records deltas and assembles partial responses in Redis
    - `/hooks/stream_replacement` provides the replacement stream when requested

Key files:
- `src/luthien_control/proxy/custom_logger.py`: very thin wrappers that forward LiteLLM hook params to Control Plane endpoints.
- `src/luthien_control/control_plane/app.py`: defines the hook endpoints and loads the active policy.
- `src/luthien_control/policies/base.py`: abstract policy class mirroring LiteLLM hooks, including per-chunk streaming control.
- `src/luthien_control/policies/noop.py`: default no-op policy implementation.

### Implement a new policy

1) Create a policy class
- Copy `NoOpPolicy` and implement the same methods:
  - `async_pre_call_hook(user_api_key_dict, cache, data, call_type)` → return None (pass), a string (reject/short-circuit), or a dict (rewritten request).
  - `async_post_call_success_hook(data, user_api_key_dict, response)` → return a dict to replace the response, or None to keep original.
  - `streaming_on_chunk(user_api_key_dict, request_data, chunk, chunk_index, accumulated_text)` → return `{ action: 'pass' | 'suppress' | 'edit' | 'replace_stream', chunk?: dict }`.
  - `streaming_replacement(request_data)` → async generator yielding OpenAI-style streaming chunks as dicts when replacing.

2) Activate your policy
- Set `LUTHIEN_POLICY` to `"module.path:ClassName"` (e.g., `export LUTHIEN_POLICY=luthien_control.policies.my_policy:MyPolicy`).
- Restart only the control plane: `docker compose restart control-plane`.

3) (Optional) Use helpers
- `src/luthien_control/policies/engine.py` for decision logging and state.
- `TrustedMonitor`/`UntrustedMonitor` for scoring/generation if needed.

### Examples

- Reject certain prompts (pre hook):
  - Return a string like "Request rejected by policy".

- Rewrite requests (pre hook):
  - Return a dict with updated `messages`, `tools`, or params.

- Replace risky responses (post hook):
  - Return a dict shaped like an OpenAI response to fully replace.

- Replace the stream (streaming decide + replacement):
  - Return `True` from `streaming_decide`, then yield chunks from `streaming_replacement`.

### Verify

- Start the stack: `./scripts/quick_start.sh`.
- Run the smoke test: `uv run python scripts/test_proxy.py`.
- Tail control-plane logs to see policy actions.

## Troubleshooting

- Codex/IDE sends a JWT instead of your LiteLLM key
  - Symptom: LiteLLM logs an error like: `user_api_key_auth(): LiteLLM Virtual Key expected... Received=eyJhbGciOi... expected to start with 'sk-'` and the received token decodes as a JWT tied to your OpenAI account (iss: https://auth.openai.com).
  - Cause: Your client (IDE/extension/OpenAI app/CLI) is using an OpenAI account session token instead of an API key. When you point that client at LiteLLM (`OPENAI_BASE_URL`), it still sends the session JWT.
  - Fix:
    - Configure the client to use “API Key” auth (not “Account login/Session”).
    - Set base URL to `http://localhost:4000/v1` (some clients require the `/v1` suffix).
    - Use a LiteLLM key as the API key:
      - Master key: the value of `LITELLM_MASTER_KEY` (e.g., `sk-luthien-dev-key`).
      - Or generate a per‑user virtual key and use that in the IDE:
        ```bash
        export LITELLM_URL=http://localhost:4000
        export LITELLM_MASTER_KEY=sk-luthien-dev-key   # or your real value
        curl -s -X POST "$LITELLM_URL/key/generate" \
          -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
          -H "Content-Type: application/json" \
          -d '{"key_alias":"codex-local","max_budget":100,"metadata":{"source":"codex"}}'
        # Response contains a `key` like `vk-...`; use that in your client.
        ```
    - If the client keeps sending a JWT, disable any “Use OpenAI account/app session” setting and sign out of OpenAI in that tool. As a last resort, remove cached OpenAI tokens (e.g., `openai logout`, clear `~/.config/openai/*`, or sign out of the OpenAI Desktop/VS Code extension), then explicitly paste the LiteLLM key.
  - Sanity check your setup with curl:
    ```bash
    curl -s http://localhost:4000/v1/models \
      -H "Authorization: Bearer $LITELLM_MASTER_KEY"
    ```
    You should get a 200 and a model list.


## License

Apache License 2.0
