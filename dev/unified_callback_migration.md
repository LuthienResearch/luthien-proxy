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
- **Unified Response**: Proxy now runs with `unified_callback`; end-to-end sanity checks and LLM-backed applications succeed using the normalized chunk format. Observability, documentation, and CI follow-ups tracked in TODO.md.
- **Unified Monitor UI**: Introduced `/ui/conversation/live` with real-time trace streaming, diffed request messages, and inline tool-call visualization backed by the structured conversation APIs.

## TODO

- **Unify response** *(see planning doc)*:
  - [ ] Observability: ensure logs/metrics capture normalized chunks and adjust dashboards/alerts.
  - [ ] Documentation: refresh README/deployment guides with unified callback workflow and dummy validation stack instructions.
  - [ ] CI/Automation: decide on running the Docker dummy e2e in CI and clean up redundant scripts/configs afterwards.
