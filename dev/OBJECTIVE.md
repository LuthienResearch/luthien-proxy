# Current Objective

Fix: Luthien proxy fails when Claude Code sends images (#103)

## Error Details

When attempting to send an image through the Luthien proxy, the request fails with:

```
API Error: 400 {"detail":"Invalid Anthropic request format: 1 validation error for Request
messages.112.content
  Input should be a valid string [type=string_type, input_value=[{'type': 'image', 'sourc...ia_type': 'image/png'}}], input_type=list]
  For further information visit https://errors.pydantic.dev/2.11/v/string_type"}
```

## Root Cause (to investigate)

The Anthropic API accepts `messages.content` as either:
- A string (simple text message)
- A list of content blocks (for multimodal: text + images)

Something in our request handling is expecting a string but receiving a list when images are included.

## Acceptance Criteria

- [ ] Images can be sent through the proxy to Claude without validation errors
- [ ] Existing text-only requests continue to work
