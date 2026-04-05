---
category: Fixes
pr: 499
---

**Streaming pipeline leaked Python SDK synthetic events to wire-protocol clients**: The Anthropic Python SDK emits synthetic helper events (`text`, `thinking`, `citation`, `signature`, `input_json`) that have no wire-protocol counterpart. These were forwarded to clients, breaking strict validators like `@ai-sdk/anthropic`. Fixed by filtering synthetic events at the SSE formatting layer and excluding them from `accumulated_events`. Also strips SDK-internal payload fields from `content_block_stop` and `message_stop` events so only the fields defined by the Anthropic wire protocol are sent.
