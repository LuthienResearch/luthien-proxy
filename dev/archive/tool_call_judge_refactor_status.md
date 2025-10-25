# Tool Call Judge Refactoring - Completed

**Date**: 2025-10-22

## Summary

Completed the planned refactoring of `tool_call_judge.py`. The file was reduced from 671 to 610 lines (-61 lines, -9%) through extraction of reusable utilities and removal of unnecessary constants. All 37 tests pass.

## Completed Work

### 1. Fixed Critical Security Bugs (Previous Session)

- **Stream-end bypass**: Tool call chunks buffered at stream end were forwarded without judge evaluation
  - Fixed in [tool_call_judge.py:203-225](../src/luthien_proxy/v2/policies/tool_call_judge.py#L203-L225)

- **Incomplete tool call handling**: Added fail-safe logic to block incomplete tool calls (missing name)
  - Added `_create_incomplete_blocked_response()` method
  - Detection logic in `_evaluate_tool_calls()` at line 306-313

- **Content after tool call not buffered**: Previous implementation didn't buffer non-tool-call content that arrived after a tool call started
  - Fixed by switching to queue-based buffering algorithm

### 2. Simplified Streaming Algorithm (Previous Session)

Replaced complex nested loops with simple queue-based algorithm documented in [dev/NOTES.md](NOTES.md#L13-L64).

### 3. Test Coverage

- All 37 tests pass (21 for tool_call_judge + 16 for uppercase_nth_word)
- Coverage: 90% of tool_call_judge.py
- **New test**: `test_content_after_tool_call_is_buffered` - regression test for content buffering bug

### 4. Refactoring Complete

#### Task 1: Add unit test for content-after-tool-call buffering ✅
- Added `test_content_after_tool_call_is_buffered` in [test_tool_call_judge.py:492-585](../tests/unit_tests/v2/policies/test_tool_call_judge.py#L492-L585)
- Tests scenario: tool_call chunk → content chunk → finish chunk
- Verifies all chunks are buffered until tool call is judged

#### Task 2: Add default pass-through implementations to base policy class ✅
- Updated [base.py](../src/luthien_proxy/v2/policies/base.py)
- Removed `@abstractmethod` decorators
- Added default implementations:
  - `process_request`: returns request unchanged
  - `process_full_response`: returns response unchanged
  - `process_streaming_response`: forwards all chunks from incoming to outgoing
- Policies now only need to override methods relevant to their functionality

#### Task 3: Create policy utils module ✅
- Created [policies/utils.py](../src/luthien_proxy/v2/policies/utils.py)
- Moved `JudgeConfig` and `JudgeResult` dataclasses
- Added comprehensive docstrings for all attributes

#### Task 4: Create utility functions for ModelResponse creation ✅
- Added to [policies/utils.py](../src/luthien_proxy/v2/policies/utils.py):
  - `create_text_response(text, model)` - creates complete (non-streaming) response
  - `create_text_chunk(text, model, finish_reason)` - creates streaming chunk
- Refactored `_create_blocked_response()` and `_create_incomplete_blocked_response()` to use utilities
- Removed now-unused `time` import from tool_call_judge.py

#### Task 5: Remove DEFAULT_MODEL and DEFAULT_API_BASE constants ✅
- Moved defaults into function signature where they're visible
- Updated [tool_call_judge.py:64-72](../src/luthien_proxy/v2/policies/tool_call_judge.py#L64-L72):
  - `model: str = "openai/judge-scorer"`
  - `api_base: str | None = "http://dummy-provider:8080/v1"`
  - `probability_threshold: float = 0.6`
- Env vars still take precedence over defaults
- Removed class constants that hid defaults

#### Task 6: Document max_tokens parameter properly ✅
- Updated all docstrings to clarify `max_tokens` is for output, not input
- Updated in:
  - Class docstring
  - `__init__` docstring
  - JudgeConfig dataclass docstring
  - Example config in file header

### Tasks Deferred

The following tasks from the original plan were deferred as premature optimization:

7. **Extract judge LLM interaction logic** - Deferred until we have another policy that needs judge functionality
8. **Extract chunk dict conversion logic** - Deferred until we see a pattern of reuse across multiple files

## File Changes Summary

```
src/luthien_proxy/v2/policies/tool_call_judge.py
  - Original: 671 lines
  - Current: 610 lines
  - Net change: -61 lines (-9%)

Changes:
  - Removed 3 class constants (DEFAULT_MODEL, DEFAULT_API_BASE, DEFAULT_THRESHOLD)
  - Moved defaults to function signature (more visible)
  - Removed time import
  - Simplified ModelResponse creation using utilities (-18 lines boilerplate)
  - Improved documentation (+4 lines)

src/luthien_proxy/v2/policies/base.py
  - Added default implementations for all abstract methods
  - Removed @abstractmethod decorators
  - Added get_available import for streaming default

src/luthien_proxy/v2/policies/utils.py (NEW)
  - JudgeConfig dataclass (with better docs)
  - JudgeResult dataclass
  - create_text_response() utility
  - create_text_chunk() utility

tests/unit_tests/v2/policies/test_tool_call_judge.py
  + 1 new test for content-after-tool-call buffering
```

## Current Status

✅ All planned tasks completed (except deferred items 7-8)
✅ All 37 tests passing
✅ File reduced by 61 lines (-9%)
✅ Code is more maintainable and reusable
✅ Documentation improved throughout

## Next Steps

None - refactoring complete. Deferred tasks (7-8) can be addressed when actual reuse is needed.
