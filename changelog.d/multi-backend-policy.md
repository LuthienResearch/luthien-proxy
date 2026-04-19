---
category: Features
---

**MultiBackendPolicy**: Fan out a single request to multiple Anthropic models and aggregate the responses into one Anthropic-compatible output. First model to start streaming is emitted live; subsequent models buffer and flush sequentially in arrival order, each labeled with a `# <model>` header. Configurable stagger (`stagger_seconds`) avoids rate-limit bursts. Per-model request shaping drops fields the target model doesn't support (e.g. `thinking`, `context_management`, `effort`, `output_config` on haiku). Tool-use blocks rendered as labeled text so a client never sees ambiguous overlapping tool calls from multiple models. Uses the caller's credential (passthrough auth) for all fan-out calls and forwards the `anthropic-beta` header (minus `context-1m-*`) to preserve OAuth flows like Claude Code.
