# Objective: Kill ConversationLoggingPolicy and move conversation logging to core infra

**Branch**: `unify_formats`

## Goal

Remove `ConversationLoggingPolicy` entirely. All conversation logging should happen in core control plane infrastructure (`hook_generic` in `hooks_routes.py`), not inside a policy.

## Current State

- `ConversationLoggingPolicy` provides request/response logging, tool call extraction, and structured JSON logs
- Multiple policies inherit from it: `ToolCallBufferPolicy`, `SQLProtectionPolicy`, `LLMJudgeToolPolicy`
- Core infra in `hooks_routes.py` already logs hooks, builds conversation events, and stores them

## What Core Infra Should Do

1. Listen for connections from callbacks  (already done)
2. Log original versions of requests and responses  (already done via debug_logs)
3. Emit live event announcements  (already done via Redis publish)
4. Pass request/response to active policy
5. Run policy and generate final version
6. Log final version + emit events  (already done)
7. Send final version back to litellm  (already done)

## What Policies Should NOT Do

- Parse/extract conversation turns
- Log structured conversation events
- Emit live events

Policies should ONLY transform payloads.

## Tasks

1. Identify what ConversationLoggingPolicy provides that isn't in core infra
2. Move any missing extraction logic to core infra if needed
3. Update `ToolCallBufferPolicy` to inherit from `LuthienPolicy` instead
4. Update policies that use ConversationLogStreamContext
5. Delete ConversationLoggingPolicy
6. Update tests
7. Verify e2e that conversation logging still works

## Acceptance

- `ConversationLoggingPolicy` deleted
- All policies inherit from `LuthienPolicy` or other non-logging base
- Conversation events still stored and retrievable
- Tests pass
