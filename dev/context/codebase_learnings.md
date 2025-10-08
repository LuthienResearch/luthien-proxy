# Codebase Learnings

Architectural patterns, module relationships, and how subsystems work together.

**Format**: Each entry is a subsection with a title, timestamp (YYYY-MM-DD), and content (bullet points or prose).
If updating existing content significantly, note it: `## Topic (2025-10-08, updated 2025-11-15)`

---

## Architecture Overview (2025-10-08)

- **Control Plane** (`src/luthien_proxy/control_plane/`): FastAPI application that makes policy decisions
- **Proxy** (`src/luthien_proxy/proxy/`): LiteLLM proxy integration with custom logging
- **Policies** (`src/luthien_proxy/policies/`): Policy implementations that receive callbacks from the proxy
- **Operational Helpers** (`scripts/`): Utility scripts such as `run_bg_command.sh` (fire-and-poll shell launcher) and `check_ui_endpoints.py` (control-plane UI smoke test).

Centralized control plane makes policy decisions, proxy stays thin and forwards callbacks.

## Key Patterns

(Add patterns as discovered during development with timestamps: YYYY-MM-DD)
