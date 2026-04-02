---
category: Features
pr: 478
---

**Conversation viewer rewrite**: SSE-powered live updates (replacing polling), per-turn event timeline with raw JSON, and JSONL export
  - New `ConversationLinkPolicy` injects viewer URL into first response per session
  - JSONL export endpoint at `GET /api/history/sessions/{id}/export/jsonl`
