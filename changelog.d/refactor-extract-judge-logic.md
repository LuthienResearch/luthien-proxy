---
category: Refactors
pr: 728
---

**Extract shared tool-use streaming buffer logic**: Pulled duplicated streaming mechanics out of `ToolCallJudgePolicy` and `DogfoodSafetyPolicy` into utility functions in `tool_call_judge_utils.py` (`BufferedToolUse`, `handle_tool_use_block_start`, `handle_tool_use_block_delta`, `build_allowed_tool_use_events`, `build_blocked_text_events`, `build_blocked_non_streaming_response`). Pure refactor — no behavior change.
