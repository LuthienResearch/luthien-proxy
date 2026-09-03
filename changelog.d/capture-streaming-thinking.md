---
category: Fixes
---

**Capture reasoning blocks from streaming responses**: `thinking` and `redacted_thinking` content blocks were dropped when rebuilding a streamed response for conversation history, so a streamed call recorded a complete-looking response with no reasoning content. The non-streaming path already stored them verbatim, so history was inconsistent between the two.
  - `thinking` blocks accumulate `thinking_delta` text and record the `signature_delta` value.
  - `redacted_thinking` blocks keep their opaque `data` payload.
  - Block ordering is unchanged, so reasoning blocks still precede visible text.
