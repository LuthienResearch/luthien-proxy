---
category: Features
pr: 693
---

**StringReplacementPolicy response-side observability**: Replaced the `policy.anthropic_string_replacement.content_transformed` event with `policy.string_replacement.response_modified`, reporting an accurate `total_replacements` count (rather than the configured-pattern count) plus `blocks_modified`, `original_length`, and `transformed_length`. Streaming responses now emit a single aggregated event at stream completion, matching the non-streaming path.
