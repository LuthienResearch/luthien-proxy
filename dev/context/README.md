# Context Directory

This directory contains persistent knowledge accumulated across development sessions, including architectural patterns, technical decisions, and gotchas discovered while working on the codebase.

## Active Documents

These documents are actively maintained and reflect current architecture:

- **[`authentication.md`](authentication.md)** - Auth modes, passthrough auth flow, OAuth, judge key resolution
- **[`codebase_learnings.md`](codebase_learnings.md)** - Architectural patterns, module relationships, how subsystems work together
- **[`decisions.md`](decisions.md)** - Technical decisions made and their rationale
- **[`gotchas.md`](gotchas.md)** - Non-obvious behaviors, edge cases, common mistakes
- **[`observability_records.md`](observability_records.md)** - Event recording patterns and observability architecture
- **[`otel-conventions.md`](otel-conventions.md)** - OpenTelemetry naming conventions and attribute schemas
- **[`sentry.md`](sentry.md)** - Sentry error tracking: integration design, data scrubbing, per-environment setup
- **[`streaming_response_structures.md`](streaming_response_structures.md)** - Streaming chunk structures and SSE format

## Historical Documents (Archived)

These documents provided important feedback during the V2 design phase but are superseded by current implementation docs:

- **[`redis-otel-analysis.md`](redis-otel-analysis.md)** - Analysis of Redis vs OpenTelemetry for real-time monitoring

**Do not update these documents.** They serve as historical reference only.

## Current Implementation

For outstanding work items, see the [Trello board](https://trello.com/b/ehoxykPf/luthien?filter=label:luthien-proxy%20TODO).

## Adding New Context

Update context files proactively during development, not just at the end of objectives:

- Add timestamps (YYYY-MM-DD) to help detect stale knowledge
- Include file paths and line numbers for code references
- Explain WHY decisions were made, not just WHAT was implemented
- Document gotchas when you encounter them, while the pain is fresh

## Reliability of Context Documents

These documents are written by both humans and AI agents during development. **Agent-written content may contain incorrect inferences presented as facts.** Known incidents:

- `authentication.md` documented prefix-based OAuth detection (`sk-ant-*`) as intentional architecture. It was actually a bug — the transport header (`Authorization: Bearer` vs `x-api-key`) is the correct discriminator. This incorrect doc caused a future agent to spend time defending the wrong approach.

**When reading these docs:**
- Treat claims about behavior as "probably true, verify before relying on"
- If something seems wrong, check the code — the code is authoritative
- When you fix incorrect information, note the correction with a timestamp
