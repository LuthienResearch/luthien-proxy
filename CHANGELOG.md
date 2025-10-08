# CHANGELOG

## DEVELOPMENT | TBA

- development framework
- litellm integration
- in-depth hook logging
- request/response inspection UX
- shared redis/db client provisioning and pooling
- refactored app.py into smaller, more focused modules
- unit test coverage >80%
- [stream-view](stream-view): Delivered live conversation trace and trace-by-call UIs with real-time SSE updates
- [stream-view](stream-view): Added configurable chunk history limits to prevent runaway memory use during long streams
- [stream-view](stream-view): Hardened streaming reliability with cleanup on disconnects and exponential backoff for reconnects
- [conversations] Switched to websockets architecture for two-way streaming between litellm and control plane, enabling arbitrary policy intervention
- [control-plane-prisma](control-plane-prisma): Migrated the control-plane database to Prisma-managed migrations with automated deploy steps in docker-compose and `scripts/quick_start.sh`
- [control-plane-prisma](control-plane-prisma): Removed unused legacy tables from the control-plane schema and unified LiteLLM Prisma assets under `prisma/litellm/`
- [dataflows-doc](dataflows-doc): Added comprehensive documentation covering Postgres and Redis dataflows, retention, and operational nuances
- [ci-prisma-validations](ci-prisma-validations): CI runs Prisma migration deploy/push against an ephemeral Postgres service to catch schema drift early
- llm-monitor: Added an LLM-backed tool-call judge that blocks risky tools in streaming and non-streaming flows and exposes decisions in the control-plane UI (src/luthien_proxy/policies/tool_call_judge.py:1, src/luthien_proxy/control_plane/templates/policy_judge.html:1).
- llm-monitor: Replaced the proxy streaming loop with a resilient orchestrator and layered callback/control-plane instrumentation so we can trace every chunk end to end (src/luthien_proxy/proxy/stream_orchestrator.py:1, src/luthien_proxy/proxy/callback_instrumentation.py:1, src/luthien_proxy/control_plane/debug_routes.py:1).
- llm-monitor: Introduced an SQL tool-call protection policy, expanded the dummy provider to drive blocking scenarios, and added full e2e coverage for policy behavior (src/luthien_proxy/policies/sql_protection.py:1, scripts/demo_lib/dummy_provider.py:1, tests/e2e_tests/test_tool_call_judge_e2e.py:1).
- [unify_formats](unify_formats): Normalized streaming responses from different LLM providers into unified OpenAI-compatible format, eliminating provider-specific logic in control plane and simplifying policy implementation (src/luthien_proxy/proxy/stream_normalization.py:1, config/unified_callback.py:1).

## 0.0.0 | 2025-11-22

- This is an example entry in an example release section (0.0.0)
- 0.0.0 designates the version number
- Update this section as objectives are completed
- Typically multiple objectives will be included in a given release
- Releases are listed in descending chronological order
- The most recent release is always at the top of the file; this should be the only entry that changes
- In development, the in-progress entry will typically be labeled as version `DEVELOPMENT` with release date `TBA`
- The in-development release section will be updated to a proper version number with release date when it's time to release a new version (this is a human decision)
- Each bullet in a release section should link back to the objective handle (e.g., `[policy-engine cleanup](policy-engine-cleanup)`) so we can trace work quickly
