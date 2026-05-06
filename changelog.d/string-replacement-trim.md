---
category: Features
pr: 693
---

**StringReplacementPolicy response-side observability**: Replaced the `policy.anthropic_string_replacement.content_transformed` event with `policy.string_replacement.response_modified`, reporting an accurate `total_replacements` count (rather than the configured-pattern count) plus `blocks_modified`, `original_length`, and `transformed_length`. Streaming responses now emit a single aggregated event at stream completion, matching the non-streaming path. Note that for overlap-prone configs (e.g. `[("ab", "ca")]`) the streaming `total_replacements` may differ from the non-streaming count on the same input — streaming reports substitutions actually performed at chunk boundaries, while non-streaming counts substitutions over the fully-assembled text. **Breaking for observability consumers**: any dashboards, alerts, or queries keyed on the old event name must be updated to the new name.
