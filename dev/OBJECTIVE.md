# Current Objective

**Branch:** `feature/conversation-log-export`

Implement automatic clean conversation log export for Luthien sessions.

## Background

During dogfooding, Scott created `scott_image_repro_clean.csv` with a clean prompt/response format useful for debugging. We want Luthien to automatically generate this format for all sessions.

## Acceptance Criteria (MVP)

- [ ] PR ready for review with working implementation
- [ ] Clean CSV/view with human-readable prompt/response format
- [ ] Update docs with database schema for this summary-level view

## Out of Scope (Future)
- Native UI for viewing logs (first try Cursor's Rainbow CSV extension)
- User-editable comments field

## Reference Fields
| Field | Include | Notes |
|-------|---------|-------|
| `created_at` | ✅ | Timestamp |
| `prompt_or_response` | ✅ | PROMPT or RESPONSE |
| `content` | ✅ | Actual text (truncated?) |
| `session_id` | ✅ | Group by session |
| `model` | ❓ | Useful for debugging |
