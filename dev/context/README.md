# Context Directory

This directory contains persistent knowledge accumulated across development sessions, including architectural patterns, technical decisions, and gotchas discovered while working on the codebase.

## Active Documents

These documents are actively maintained and reflect current architecture:

- **[`codebase_learnings.md`](codebase_learnings.md)** - Architectural patterns, module relationships, how subsystems work together
- **[`decisions.md`](decisions.md)** - Technical decisions made and their rationale
- **[`gotchas.md`](gotchas.md)** - Non-obvious behaviors, edge cases, common mistakes
- **[`observability-guide.md`](observability-guide.md)** - General observability patterns and OpenTelemetry setup
- **[`otel-conventions.md`](otel-conventions.md)** - OpenTelemetry naming conventions and attribute schemas

## Historical Documents (Archived)

These documents provided important feedback during the V2 design phase but are superseded by current implementation docs:

- **[`observability_review_summary.md`](observability_review_summary.md)** - Summarized architectural decisions during observability v2 planning
- **[`observability-architecture-proposal.md`](observability-architecture-proposal.md)** - Original four-layer architecture proposal
- **[`redis-otel-analysis.md`](redis-otel-analysis.md)** - Analysis of Redis vs OpenTelemetry for real-time monitoring

**Do not update these documents.** They serve as historical reference only.

## Current Implementation

For the current V2 implementation status and plans, see:
- [`dev/v2_architecture_design.md`](../v2_architecture_design.md) - **Architecture design and implementation status**
- [`dev/observability-v2.md`](../observability-v2.md) - **Observability features and status**
- [`dev/TODO.md`](../TODO.md) - **Outstanding work items**

## Adding New Context

Update context files proactively during development, not just at the end of objectives:

- Add timestamps (YYYY-MM-DD) to help detect stale knowledge
- Include file paths and line numbers for code references
- Explain WHY decisions were made, not just WHAT was implemented
- Document gotchas when you encounter them, while the pain is fresh
