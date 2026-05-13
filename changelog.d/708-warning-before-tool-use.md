---
category: Fixes
---

**SimpleLLMPolicy: enforce tool_use-trailing invariant; truncate on first block** (#708)
  - Previously, when the judge was unreachable and `on_error="pass"`, the
    judge-unavailable warning text block was appended after any emitted
    `tool_use`. On the next turn the Anthropic API rejected the conversation
    with `messages.X: tool_use ids were found without tool_result blocks
    immediately after`, bricking the session (`API Error: 400 due to tool
    use concurrency issues`).
  - The warning is now injected before the first `tool_use`. More generally,
    the policy now refuses to emit non-`tool_use` content after the first
    `tool_use` from every emission site (streaming pass/replace/block,
    non-streaming, message_delta fallback). This closes a class of bugs
    reachable by:
      * parallel-tool responses where the judge fails on a non-first tool
        (warning would have landed between tools — 400),
      * the judge action `"block"` on a non-first tool (blocked-text marker
        would have followed a prior `tool_use` — 400),
      * upstream models emitting any text after a `tool_use` in a single
        response (rare, but possible).
    Empirically verified against the live Anthropic API for all variants
    (see `tests/luthien_proxy/e2e_tests/real_anthropic/probe_tool_use_invariant.py`).
  - Block-truncation: when the judge blocks a tool, all subsequent tools in
    the same response are dropped without being judged. Partial intervention
    has no clean way to communicate to the next turn (the "[Tool X was
    blocked]" marker can't follow a prior tool_use under the invariant), so
    truncating at the first block keeps intent obvious to the model.
  - Test harness: `ClaudeCodeSimulator` now preserves wire-order block layout
    when reconstructing assistant content. The previous behavior — merging
    text blocks and grouping `tool_use` after text — silently corrected
    malformed proxy output before it reached turn 2, hiding this class of bug.
