---
category: Chores & Docs
---

**Regression suite for known-bad API request patterns (COE audit)**: Adds 19 unit tests pinning how the transparency-first pipeline handles the request patterns that caused production 400s in the LiteLLM era (empty text blocks PR #201, orphaned tool_results PR #167, cache_control extra fields PR #178, context_management PR #151, duplicate tools, parallel tool_use ordering PR #356). Verified against the live Anthropic API on 2026-07-06: bad patterns are forwarded verbatim and upstream 400s are relayed cleanly; context_management is now a real API feature that must be forwarded, and whitespace-only text blocks are now accepted upstream.
