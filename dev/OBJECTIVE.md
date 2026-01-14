# Objective

Fix thinking blocks being stripped from non-streaming responses, causing 500 errors.

**Issue:** https://github.com/LuthienResearch/luthien-proxy/issues/128

## Problem

When `thinking` is enabled in requests, `openai_to_anthropic_response()` strips thinking blocks from the response, causing the Anthropic API to reject it with:

```
messages.1.content.0.type: Expected `thinking` or `redacted_thinking`, but found `text`.
```

## Fix

In `openai_to_anthropic_response()`:
1. Check for `message.thinking_blocks` (from LiteLLM)
2. Add thinking blocks FIRST in the content array (required by Anthropic API)
3. Then add text content
4. Then add tool calls

## Acceptance Criteria

- [ ] Non-streaming requests with `thinking` enabled return valid responses
- [ ] Thinking blocks appear first in response content array
- [ ] Unit tests cover thinking block extraction
- [ ] E2E test with real API call confirms fix
