---
category: Fixes
pr: 499
---

**Streaming pipeline leaked Python SDK synthetic events to wire-protocol clients**: The Anthropic Python SDK's high-level `MessageStream` injects synthetic helper events (`text`, `thinking`, `citation`, `signature`, `input_json`) that have no wire-protocol counterpart. These were forwarded to clients, breaking strict validators like `@ai-sdk/anthropic`. Fixed by switching from `messages.stream()` (high-level `MessageStream` with synthetic events) to `messages.create(stream=True)` (raw `AsyncStream[RawMessageStreamEvent]` yielding only wire-protocol events). This eliminates the problem structurally — no blocklist/allowlist maintenance required.
