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

### Outstanding Work:
- Delete V1-specific Docker files (Dockerfile.litellm, Dockerfile.control-plane if unused)
- Update scripts that reference port 4000/8081 or old services
- Clean up V1-specific source code in src/luthien_proxy/control_plane/ and src/luthien_proxy/proxy/
