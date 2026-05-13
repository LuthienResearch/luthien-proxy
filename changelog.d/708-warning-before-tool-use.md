---
category: Fixes
---

**SimpleLLMPolicy: response composition lifted into AnthropicMessageBuilder; tool_use-trailing invariant enforced by construction** (#708)
  - Previously, when the judge was unreachable and `on_error="pass"`, the
    judge-unavailable warning text block was appended after any emitted
    `tool_use`. On the next turn the Anthropic API rejected the conversation
    with `messages.X: tool_use ids were found without tool_result blocks
    immediately after`, bricking the session (`API Error: 400 due to tool
    use concurrency issues`).
  - Yash hit this on the RealPage 2026-05-06 trial. Empirically verified
    against the live Anthropic API on 2026-05-13 (see
    `tests/luthien_proxy/e2e_tests/real_anthropic/probe_tool_use_invariant.py`
    for the case matrix).
  - Root cause was structural: the policy was composing the wire response
    inline at four scattered emission sites, each having to remember to
    enforce the trailing-tool_use invariant. Multiple paths (replacement
    creating a tool_use, late judge failures, multi-block sequences) could
    silently violate it.
  - **Fix**: introduce `policy_core.AnthropicMessageBuilder` which owns
    Anthropic-streaming concerns end-to-end — upstream block buffering,
    downstream wire composition, index allocation, the trailing-tool_use
    invariant. `tool_use` decisions buffer until `finalize()`; text and
    passthrough blocks emit immediately if no tool has been buffered yet,
    otherwise queue for emission *before* the tool flush. The wire
    invariant is true by construction; it cannot be violated regardless of
    when warnings or markers are noted relative to the tool stream.
  - SimpleLLMPolicy is now thin: dispatch each upstream event to the
    builder, judge complete blocks, register decisions. The state struct
    drops `tool_use_emitted`, `tool_blocking_engaged`, `index_shift`,
    `emitted_blocks`, `warning_emitted`, the upstream `text_buffer` and
    `tool_buffer` — all subsumed by the builder.
  - **Behavior change**: subsequent tools after a `block` decision are now
    judged independently rather than silently dropped. The consolidated
    blocked-tools marker emits in the pre-tool slot at finalize, so a
    blocked tool no longer prevents a passing tool in the same response
    from going through. Each tool is judged once.
  - **Behavior change**: text blocks that arrive after a tool_use in the
    upstream stream are now reordered into the pre-tool region rather than
    dropped. Both blocks are preserved on the wire.
  - Test harness: `ClaudeCodeSimulator` now preserves wire-order block
    layout when reconstructing assistant content. The previous behavior —
    merging text blocks and grouping `tool_use` after text — silently
    corrected malformed proxy output before it reached turn 2, hiding this
    class of bug.
