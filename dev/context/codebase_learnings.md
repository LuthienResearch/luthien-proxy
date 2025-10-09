# Codebase Learnings

Architectural patterns, module relationships, and how subsystems work together.

**Format**: Each entry is a subsection with a title, timestamp (YYYY-MM-DD), and content (bullet points or prose).
If updating existing content significantly, note it: `## Topic (2025-10-08, updated 2025-11-15)`

---

## Architecture Overview (2025-10-08, updated 2025-11-30)

- **Control Plane** (`src/luthien_proxy/control_plane/`): FastAPI application that makes policy decisions
- **Proxy** (`src/luthien_proxy/proxy/`): LiteLLM proxy integration with custom logging
- **Policies** (`src/luthien_proxy/policies/`): Policy implementations that receive callbacks from the proxy
- **Operational Helpers** (`scripts/`): Utility scripts such as `run_bg_command.sh` (fire-and-poll shell launcher); UI test harnesses now live in the e2e suite.

Centralized control plane makes policy decisions, proxy stays thin and forwards callbacks.

## Key Patterns (2025-10-08)

- Structured conversation storage: `conversation_calls`, `conversation_events`, and `conversation_tool_calls` tables capture canonical history. APIs now read from these tables instead of replaying `debug_logs`, while Redis still handles live SSE fan-out.

(Add additional patterns as discovered during development with timestamps.)

## Conversation Turn Roadmap (2025-10-09)

- Control-plane ingestion already sees normalized OpenAI-style payloads (requests + completions) via `unified_callback`; future work will derive canonical conversation turns directly from these events.
- Planned storage evolution: replace per-tool/judge tables with per-turn records (`conversation_turns`) plus optional policy annotations, linked into threads for branching and hashed via user-history + assistant-final text.
- Live monitor v2 targets chat-style rendering by streaming turns (request originals vs. final policy output) and inline tool-call details from the same structured records.
