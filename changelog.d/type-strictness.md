---
category: Refactors
pr: 461
---

**Type strictness pass**: Replace `Any` with concrete types (`AnthropicContentBlock`, `ToolCallDict`, `JSONObject`, `AnthropicRequest`) across 12 files; remove 208 lines of dead code (`transaction_recorder.py`). Also fixes a latent `AttributeError` in `PolicyContext.__deepcopy__` where `.model_copy()` was called on a non-Pydantic field.
