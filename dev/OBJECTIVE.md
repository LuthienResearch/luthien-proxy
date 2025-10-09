# Objective: Remove ConversationLoggingPolicy

**Goal:** Remove ConversationLoggingPolicy entirely. It produces redundant logs - the core hooks infrastructure already captures original→final versions in conversation_events and debug_logs.

**Acceptance:**
- [ ] ConversationLoggingPolicy file deleted
- [ ] All references removed from codebase
- [ ] Tests removed
- [ ] All tests pass
- [ ] Format and lint clean
