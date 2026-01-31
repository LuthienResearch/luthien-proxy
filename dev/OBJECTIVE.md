# Objective: Workflow Enforcement Policy

**Trello:** https://trello.com/c/ZwB2o7Ro

## Goal

Create a policy that enforces development workflows by validating each tool call against a workflow specification. Uses the ToolCallJudgePolicy pattern.

## Context

From conversation with Jai (2026-01-27): For each tool call, a judge validates whether it corresponds to the workflow and whether prerequisite steps have been completed based on tool call history.

## Requirements

1. **Pattern**: Extend ToolCallJudgePolicy (confirmed exists at `src/luthien_proxy/policies/tool_call_judge_policy.py`)
2. **Core behavior**: For each tool call, judge validates:
   - Does this tool call correspond to a step in the workflow?
   - Have prerequisite steps been completed based on tool call history?
3. **What the judge sees**: Only (a) history of tool calls in session, and (b) workflow spec string
4. **Workflow sources** (merged, with priority):
   - Primary: Luthien's workflow (`/dev/OBJECTIVE.md`, `/dev/context/gotchas.md`)
   - Secondary: User's personal workflow (from `CLAUDE.md`)
5. **Action on violation**: WARN (coaching, not blocking)

## Session Boundaries

Sessions are determined by existing Luthien infrastructure (see `src/luthien_proxy/pipeline/session.py`):

- **Anthropic format**: Extracted from `metadata.user_id` field with pattern `_session_<uuid>`
- **OpenAI format**: Via `x-session-id` request header

The policy will use `context.session_id` from `PolicyContext` to track tool call history per session. Tool call history will be stored in Redis with session-scoped keys (e.g., `workflow:session:<session_id>:tool_calls`).

## Workflow Spec Schema

Workflow specifications are defined in YAML with the following structure:

```yaml
# Example workflow spec
workflow:
  name: "Luthien Development Workflow"
  description: "Standard workflow for developing features"

  steps:
    - id: "set_objective"
      name: "Set objective"
      description: "Update dev/OBJECTIVE.md with clear goal"
      tools: ["Write", "Edit"]  # Tool names that satisfy this step
      file_patterns: ["**/OBJECTIVE.md", "**/dev/OBJECTIVE.md"]

    - id: "create_branch"
      name: "Create feature branch"
      description: "Create and checkout a feature branch"
      tools: ["Bash"]
      command_patterns: ["git checkout -b", "git switch -c"]
      prerequisites: ["set_objective"]  # Must complete before this step

    - id: "write_tests"
      name: "Write tests"
      description: "Add unit tests for new functionality"
      tools: ["Write", "Edit"]
      file_patterns: ["**/tests/**", "**/test_*.py"]
      prerequisites: ["create_branch"]

    - id: "implement"
      name: "Implement feature"
      description: "Write the implementation code"
      tools: ["Write", "Edit"]
      file_patterns: ["**/src/**"]
      prerequisites: ["write_tests"]  # TDD: tests first

    - id: "run_checks"
      name: "Run dev checks"
      description: "Run formatting, linting, and tests"
      tools: ["Bash"]
      command_patterns: ["dev_checks.sh", "pytest", "ruff"]
      prerequisites: ["implement"]

    - id: "commit"
      name: "Commit changes"
      description: "Commit with descriptive message"
      tools: ["Bash"]
      command_patterns: ["git commit", "git add"]
      prerequisites: ["run_checks"]

  # Steps that can happen anytime (not workflow-ordered)
  always_allowed:
    - tools: ["Read", "Glob", "Grep"]  # Reading is always OK
    - tools: ["Bash"]
      command_patterns: ["git status", "git log", "git diff"]  # Info commands
```

**Key schema elements:**
- `steps[].id`: Unique identifier for referencing in prerequisites
- `steps[].tools`: List of tool names (e.g., "Write", "Bash", "Edit")
- `steps[].file_patterns`: Optional glob patterns for file-based validation
- `steps[].command_patterns`: Optional patterns for Bash command validation
- `steps[].prerequisites`: List of step IDs that must be completed first
- `always_allowed`: Tool calls that bypass workflow ordering

## Acceptance Criteria

- [ ] Policy configurable via YAML (following existing patterns)
- [ ] Reads workflow spec from config or file paths
- [ ] Validates each tool call against workflow
- [ ] Tracks tool call history within session (Redis-backed)
- [ ] Provides actionable guidance on violations
- [ ] Logs decisions for observability
- [ ] Unit tests cover: valid call following workflow, out-of-order call, unrelated call
- [ ] Passes `./scripts/dev_checks.sh`
