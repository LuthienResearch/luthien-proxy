---
category: Chores & Docs
pr: 532
---

**Cleanup stale LiteLLM content in dev/context/ and dev/REQUEST_PROCESSING_ARCHITECTURE.md**: the agent-facing context docs still described a LiteLLM-backed gateway path that was replaced by direct Anthropic SDK usage. Rewrote the architecture overview, streaming-pipeline notes, and thinking-block gotchas to reflect the current `pipeline/anthropic_processor.py` + `AnthropicClient` path, and scoped the remaining LiteLLM references to the judge-LLM path (`llm/judge_client.py`, `simple_llm_utils.py`, `tool_call_judge_utils.py`) where it is still used.
  - Also regenerated `.env.example` as a bootstrap fix: commit 1b28e4d5 had truncated it to 0 bytes, which broke the `dev_checks.sh` clean-tree gate on every branch off main. Included here so this docs PR could pass its own CI.
