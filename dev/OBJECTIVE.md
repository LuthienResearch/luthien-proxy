# Objective: Fix orphaned tool_result after /compact

## Problem for Luthien users
Claude Code users get error after extended sessions: "unexpected tool_use_id found in tool_result blocks. Each tool_result block must have a corresponding tool_use block in the previous message."

This breaks Claude Code workflows, especially after using `/compact` which likely removes the tool_use but leaves the tool_result.

## Acceptance Criteria
- [ ] Claude Code can use `/compact` without triggering orphaned tool_result errors
- [ ] Add pruning for orphaned tool_results (opposite of PR #166's tool_call pruning)
- [ ] Unit tests cover the new pruning logic

## Technical Notes
- Error comes from Anthropic API rejecting malformed message history
- Related to PR #166 which handles the opposite direction (tool_calls without results)
- Likely needs bidirectional pruning in `processor.py`
