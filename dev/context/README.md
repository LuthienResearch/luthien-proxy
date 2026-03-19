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
