# Notes

_This file is used for scratchpad notes during active development. It is cleared when wrapping up objectives._

---

**For current implementation status**, see:
- [`dev/v2_architecture_design.md`](v2_architecture_design.md) - V2 architecture and implementation status
- [`dev/observability-v2.md`](observability-v2.md) - Observability implementation status
- [`dev/event_driven_policy_guide.md`](event_driven_policy_guide.md) - EventDrivenPolicy DSL guide

---

## Current Session: V1 Cleanup - Remove V1 Services and Config

### Part 1: dev/ Directory Cleanup (Completed)

Archived 7 completed plans to dev/archive/. Created ARCHITECTURE.md with core principles.
Updated VIEWING_TRACES_GUIDE.md and TODO.md for V2.

### Part 2: V1 Service Removal (Current)

**Deleted:**
- `config/luthien_config.yaml` - V1 policy config file

**Removed from docker-compose.yaml:**
- `litellm-proxy` service (V1 LiteLLM proxy at port 4000)
- `control-plane` service (V1 control plane at port 8081)

**Removed from .env.example:**
- `LITELLM_MASTER_KEY`, `LITELLM_PORT`, `LITELLM_LOG` (V1 proxy vars)
- `CONTROL_PLANE_URL`, `CONTROL_PLANE_PORT`, `LOG_LEVEL` (V1 control plane vars)
- `LUTHIEN_POLICY_CONFIG` (replaced with `V2_POLICY_CONFIG`)
- `CONTROL_PLANE_STREAM_TIMEOUT` (V1-specific)

**Updated:**
- `tests/conftest.py` - Changed default from `LUTHIEN_POLICY_CONFIG` to `V2_POLICY_CONFIG`
- `CLAUDE.md` - Updated policy config references and example
- `AGENTS.md` - Updated policy config references and example

**Remaining V2 Infrastructure:**
- `v2-gateway` service at port 8000 (integrated FastAPI + LiteLLM)
- `local-llm` service for policy judging
- `V2_POLICY_CONFIG` pointing to `config/v2_config.yaml`

### Part 3: Public Documentation Cleanup (Completed)

**Archived V1 docs to docs/archive/**:
- v1-reading-guide.md (248 lines - extensive V1 flow diagrams)
- v1-developer-onboarding.md (294 lines - V1 onboarding guide)
- v1-diagrams.md (300 lines - V1 Mermaid diagrams)
- v1-ARCHITECTURE.md (126 lines - V1 architecture overview)

All 4 files had 10+ references to deleted V1 modules (unified_callback, hooks_routes, streaming_routes, etc.)

**Updated README.md**:
- Fixed documentation links to point to dev/ docs instead of archived docs/
- Updated custom policy example to use EventDrivenPolicy DSL

### Summary

**Total cleanup:**
- Deleted: 1 config file (luthien_config.yaml)
- Removed: 2 Docker services (litellm-proxy, control-plane)
- Removed: 8 environment variables (V1-specific)
- Archived: 11 planning docs (dev/archive/)
- Archived: 4 public docs (docs/archive/)
- Updated: 10 files (env, compose, tests, project docs)
- Created: 1 new doc (dev/ARCHITECTURE.md)

**V2-only codebase:**
- Single service: v2-gateway at port 8000
- Single config: V2_POLICY_CONFIG → config/v2_config.yaml
- Clean documentation pointing to active V2 features

### Part 4: Scripts Directory Cleanup (Current)

**Deleted 16 obsolete scripts**:
- V1-specific: `build_replay_examples.py`, `dummy_control_plane.py`, `export_replay_logs.sh`
- Demo artifacts: `demo_check_integrity.py`, `demo_seed_db.py`, `run_demo.py`, `run_demo_manual.sh`
- One-off spikes: `capture_chunks.py`, `test_anthropic_streaming.py`, `test_judge_streaming.py`, `test_response_structures.py`
- Low-value tools: `generate_test_requests.py`, `test_v2_proxy.py`, `hook_trace_db.py`, `list_hooks.py`, `run_bg_command.sh`

**Kept 14 essential scripts**:

*Infrastructure (7)*:
- `quick_start.sh` - Main deployment
- `launch_claude_code.sh`, `launch_codex.sh` - Developer tools
- `dev_checks.sh` - CI checks
- `format_all.sh` - Code formatting
- `observability.sh` - Observability stack
- `start_v2_gateway.sh` - V2 service management

*Testing & debugging (7)*:
- `test_v2_gateway.sh` - V2 integration testing
- `test_proxy.py` - Generic proxy testing
- `sanity_hooks.py` - Sanity checks
- `query_debug_logs.py` - Database inspection
- `psql.py` - Database access
- `trace_hooks.py` - Hook tracing
- `run_dummy_provider.py` - Used by dummy-provider docker service

### Outstanding V1 Cleanup (for future PRs):
- V1 source code still exists in src/luthien_proxy/control_plane/ and src/luthien_proxy/proxy/
- V1 test files still exist (marked as deleted in the 18K line removal PR)
- Dockerfile.litellm not used but not harmful to keep
