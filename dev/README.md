# Development Documentation

Welcome to the Luthien Proxy development documentation. This directory contains guides, architectural documentation, and development tracking files to help you understand and contribute to the codebase.

## Quick Navigation

### New to the Codebase?

**Start here**: [REQUEST_PROCESSING_ARCHITECTURE.md](REQUEST_PROCESSING_ARCHITECTURE.md) - Comprehensive overview of how requests flow through the V2 gateway, from initial validation through response delivery.

Then explore:

- [context/codebase_learnings.md](context/codebase_learnings.md) - Architectural patterns and module relationships
- [context/decisions.md](context/decisions.md) - Technical decisions and their rationale
- [context/gotchas.md](context/gotchas.md) - Non-obvious behaviors and common mistakes

### Active Development

- **[TODO.md](TODO.md)** - Current backlog and known issues (check here for work that needs doing)
- **[OBJECTIVE.md](OBJECTIVE.md)** - Current objective being worked on (empty when between objectives)
- **[NOTES.md](NOTES.md)** - Implementation scratchpad for active objective

### Understanding the Architecture

- **[REQUEST_PROCESSING_ARCHITECTURE.md](REQUEST_PROCESSING_ARCHITECTURE.md)** - Complete request/response lifecycle including streaming pipeline
  - Request flow stories (non-streaming and streaming)
  - Pipeline architecture (PolicyExecutor → ClientFormatter)
  - Policy hook points
  - Component locations
  - Performance characteristics

### Observability & Debugging

Choose the guide that matches your goal:

- **New to observability?** → [OBSERVABILITY_DEMO.md](OBSERVABILITY_DEMO.md)
  *Step-by-step walkthrough using the UppercaseNthWordPolicy to explore all V2 observability features*

- **Need to view a specific trace?** → [VIEWING_TRACES_GUIDE.md](VIEWING_TRACES_GUIDE.md)
  *Quick reference for accessing traces in Tempo*

- **Understanding the observability system?** → [observability.md](observability.md)
  *Complete architecture, implementation status, and design decisions*

### Context Files (Persistent Knowledge)

The `context/` directory contains knowledge accumulated across development sessions:

- **[codebase_learnings.md](context/codebase_learnings.md)** - Architectural patterns, how subsystems work together (updated: 2025-11-05)
- **[decisions.md](context/decisions.md)** - Technical decisions with rationale and timestamps (updated: 2025-11-05)
- **[gotchas.md](context/gotchas.md)** - Non-obvious behaviors, edge cases, testing pitfalls (updated: 2025-10-24)

**Reference Material**:

- [otel-conventions.md](context/otel-conventions.md) - OpenTelemetry naming conventions for adding spans/attributes
- [redis-otel-analysis.md](context/redis-otel-analysis.md) - Redis instrumentation patterns
- [streaming_response_structures.md](context/streaming_response_structures.md) - Empirical streaming data from various LLM providers
- [anthropic_streaming_chunks.txt](context/anthropic_streaming_chunks.txt) - Example Anthropic streaming chunks
- [gpt_streaming_chunks.txt](context/gpt_streaming_chunks.txt) - Example OpenAI streaming chunks

See [context/README.md](context/README.md) for guidelines on what goes where.

## Development Workflow

This project follows a structured workflow documented in the root [CLAUDE.md](../CLAUDE.md). Key points for LLM-assisted workflows:

1. **Start an objective**: Update `OBJECTIVE.md`, create feature branch, open draft PR
2. **Develop iteratively**: Format + test frequently (`./scripts/dev_checks.sh`), commit in small chunks
3. **Update context proactively**: Add learnings to `context/` files as you discover them
4. **Complete the objective**: Update `CHANGELOG.md`, clear `OBJECTIVE.md` and `NOTES.md`, mark PR ready

### Key Commands

```bash
# Format everything
./scripts/format_all.sh

# Full dev checks (format + lint + tests + type check)
./scripts/dev_checks.sh

# Quick unit tests
uv run pytest tests/unit_tests

# E2E tests (slow, use sparingly)
uv run pytest -m e2e
```

## Architecture Overview

The V2 gateway is a FastAPI application with integrated LiteLLM and a streaming pipeline for policy enforcement:

```
Client Request
    ↓
V2 Gateway (gateway_routes.py)
    ↓
Policy Orchestrator
    ↓
Backend LLM (via LiteLLM)
    ↓
Streaming Pipeline:
  1. PolicyExecutor (block assembly + policy hooks)
  2. ClientFormatter (SSE conversion)
    ↓
Client Response
```

**Key directories**:

- `src/luthien_proxy/` - Gateway implementation
  - `control/` - Policy orchestration
  - `policies/` - Event-driven policy implementations
  - `streaming/` - Streaming pipeline components
  - `storage/` - Event persistence
  - `ui/` - Monitoring interfaces
  - `debug/` - Debug endpoints

## Testing Philosophy

- **Unit tests** (`tests/unit_tests/`) - Fast, isolated component tests
- **Integration tests** (`tests/integration_tests/`) - Test component interactions
- **E2E tests** (`tests/e2e_tests/`) - Full system tests with real LLMs (slow, use sparingly)

Run the full test suite before opening PRs for review: `./scripts/dev_checks.sh`

## Archive

Historical planning documents have been moved to the private [luthien-org](https://github.com/LuthienResearch/luthien-org) repo under `claude-code-docs/archive/`. This keeps the public repo focused on active documentation while preserving historical context for team members.

## Contributing to Documentation

### When to Update Context Files

- **codebase_learnings.md**: When you discover architectural patterns, module relationships, or understand how subsystems work
- **decisions.md**: When a technical decision is made (include rationale and alternatives considered)
- **gotchas.md**: When you encounter non-obvious behavior, edge cases, or common mistakes

**Always include timestamps** (YYYY-MM-DD) when adding entries so we know when knowledge may be stale.

### Documentation Hygiene

- Keep `OBJECTIVE.md` and `NOTES.md` clear between objectives (per workflow)
- Archive completed planning docs to [luthien-org](https://github.com/LuthienResearch/luthien-org) after objective completion
- Link to specific files with line numbers when referencing code (e.g., `[gateway_routes.py:42](../src/luthien_proxy/gateway_routes.py#L42)`)

---

**Last Updated**: 2025-11-06
