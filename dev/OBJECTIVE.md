# Objective: Workflow Enforcement Policy

**Trello:** https://trello.com/c/ZwB2o7Ro

## Goal

Create a policy that enforces development workflows by validating each tool call against a workflow specification. Uses the ToolCallJudgePolicy pattern.

## Context

From conversation with Jai (2026-01-27): For each tool call, a judge validates whether it corresponds to the workflow and whether prerequisite steps have been completed based on tool call history.

## Requirements

1. **Pattern**: Extend ToolCallJudgePolicy
2. **Core behavior**: For each tool call, judge validates:
   - Does this tool call correspond to a step in the workflow?
   - Have prerequisite steps been completed based on tool call history?
3. **What the judge sees**: Only (a) history of tool calls in session, and (b) workflow spec string
4. **Workflow sources** (merged, with priority):
   - Primary: Luthien's workflow (`/dev/OBJECTIVE.md`, `/dev/context/gotchas.md`)
   - Secondary: User's personal workflow (from `CLAUDE.md`)
5. **Action on violation**: WARN (coaching, not blocking)

## Acceptance Criteria

- [ ] Policy configurable via YAML (following existing patterns)
- [ ] Reads workflow spec from config or file paths
- [ ] Validates each tool call against workflow
- [ ] Tracks tool call history within session
- [ ] Provides actionable guidance on violations
- [ ] Logs decisions for observability
- [ ] Unit tests cover: valid call following workflow, out-of-order call, unrelated call
- [ ] Passes `./scripts/dev_checks.sh`
