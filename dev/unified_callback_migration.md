# Unified Callback Migration Notes

## Completed

- **Stream Normalisation**: Added `src/luthien_proxy/proxy/stream_normalization.py` with adapters normalising Anthropic SSE payloads into OpenAI chat chunks (and a reverse path for completeness). Implementation trimmed to the concrete payload types seen in production.
- **Unified LiteLLM Callback**: Introduced `config/unified_callback.py`, which always emits canonical OpenAI-style chunk payloads and routes Anthropic streams through the normalisation adapter before forwarding to the control plane.
- **Dummy Test Harness**:
  - Created `docker-compose.dummy.yaml` and `config/litellm_config_unified.yaml` to spin up a proxy + dummy control plane + dummy provider stack that exercises the unified callback.
  - Added `scripts/dummy_control_plane.py` (echo hooks/WebSocket traffic, exposes `/health`).
  - Added `tests/e2e_tests/test_unified_callback_dummy.py` that launches the dummy stack, sends a streaming request, and asserts the returned chunks match the OpenAI schema.
- **Documentation Inline**: Documented the canonical chunk structure inside `unified_callback.py` so future changes can reference a single source of truth.
- **Control Plane Migration**: Validated incoming/outgoing WebSocket chunks in `streaming_routes.py`, ensuring only OpenAI-style payloads reach policies, and added regression tests for the canonicaliser.

## TODO

- **Default Proxy Config Switch**: Once the control plane is ready, point `config/litellm_config.yaml` (and deployment manifests) at `unified_callback` and phase out the legacy callback/replay wiring.
- **Observability Pass**: Ensure logs/metrics capture the normalised chunks (stream IDs, tool events, etc.) and adjust dashboards/alerts if necessary.
- **Documentation Refresh**: Update README/deployment guides to describe the unified callback, the dummy stack for quick validation, and any configuration/env changes required.
- **CI/Automation**: Decide whether to incorporate the dummy-stack e2e into CI (Docker-dependent) or leave it as an on-demand regression; remove redundant scripts/configs after the migration stabilises.
