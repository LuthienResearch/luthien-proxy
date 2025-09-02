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
    - `/hooks/stream_chunk` is called for every chunk and can pass/suppress/edit or cut over to replacement
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

## License

Apache License 2.0
