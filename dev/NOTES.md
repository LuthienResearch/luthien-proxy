# E2E Test Investigation Report

**Date**: 2026-02-03
**Branch**: anthropic-e2e-fixes

## Summary

Investigated the failing E2E tests and related bugs. Found **4 distinct issues**:

| Issue | Status | Action Required |
|-------|--------|-----------------|
| Docker env var override | **FIXED** | Merged to this branch |
| Anthropic metadata rejection | Understood | Update tests |
| Cross-format routing (Anthropic→OpenAI) | Expected | Phase 2 work |
| /compact duplicate tools | Unmerged PRs | Merge PRs #161, #167 |

---

## Issue 1: Docker Environment Variable Override (FIXED)

### Symptom
All Anthropic E2E tests failing with 401 authentication errors: "invalid x-api-key"

### Root Cause
Shell environment variables were overriding `.env` file values. Docker Compose's `${VAR}` syntax checks shell first, `.env` second.

**Evidence:**
- `.env` file had key `sk-ant-api03-fDX_...`
- Shell environment had different key `sk-ant-api03-4I7...`
- Docker container inherited shell value (wrong key)

### Fix Applied
Modified `docker-compose.yaml` to use `env_file: .env` and removed `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` from the explicit `environment:` block. API keys now load directly from `.env` without shell interference.

```yaml
# Before (broken)
environment:
  - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}  # Shell wins over .env

# After (fixed)
env_file: .env  # API keys loaded directly from .env
environment:
  # API keys NOT listed here - loaded via env_file
```

### Verification
After fix: 12 of 16 `test_extra_params.py` tests pass (was 8 failures, now 2).

---

## Issue 2: Anthropic Metadata Rejection

### Symptom
Tests fail with 400 error: `metadata.custom_field: Extra inputs are not permitted`

### Root Cause
Anthropic API changed - now only accepts specific metadata fields (e.g., `user_id`). Custom fields like `{"custom_field": "value"}` are rejected.

### Evidence
```json
// Request
"metadata": {"user_id": "test-user-123", "custom_field": "custom_value"}

// Error from Anthropic
{"type": "invalid_request_error", "message": "metadata.custom_field: Extra inputs are not permitted"}
```

### Affected Tests
- `test_anthropic_metadata_parameter_accepted` (`test_extra_params.py:153`)
- `test_anthropic_client_to_openai_backend_with_extra_params` (`test_extra_params.py:485`)

### Proposed Fix
Update tests to only use allowed metadata fields:
```python
# Before
"metadata": {"user_id": "test-user-123", "custom_field": "custom_value"}

# After
"metadata": {"user_id": "test-user-123"}
```

---

## Issue 3: Cross-Format Routing Not Implemented

### Symptom
`test_anthropic_client_openai_backend_non_streaming` fails with 404: "model: gpt-3.5-turbo"

### Root Cause
The split-APIs architecture (PR #169) uses **endpoint-based routing**, not **model-based routing**:
- `/v1/messages` → Always uses Anthropic client
- `/v1/chat/completions` → Always uses OpenAI/LiteLLM client

Sending `gpt-3.5-turbo` (OpenAI model) to `/v1/messages` (Anthropic endpoint) fails because the request goes to Anthropic's API, which doesn't know that model.

### Architecture Details
```
/v1/messages → anthropic_processor.py → AnthropicClient → Anthropic API
/v1/chat/completions → processor.py → LiteLLMClient → OpenAI/Other APIs
```

There is **NO model-based routing** at the gateway level. This is noted as Phase 2 work in the split-APIs design.

### Affected Tests
- `test_anthropic_client_openai_backend_non_streaming` (`test_gateway_matrix.py:252`)

### Proposed Fix
Either:
1. **Skip/remove test** - This functionality isn't supported yet
2. **Implement Phase 2** - Add model-based routing with format conversion

---

## Issue 4: /compact Duplicate Tool Names

### Symptom
Claude Code's `/compact` command fails when going through Luthien proxy with: "Tool names must be unique"

### Root Cause
Two separate issues after `/compact`:

1. **Duplicate tool names**: After compacting, the tools array can contain duplicate tool names
   - OpenAI accepts duplicates
   - Anthropic rejects: "tools: Tool names must be unique"

2. **Orphaned tool_results**: `/compact` may remove `tool_use` blocks but leave orphaned `tool_result` blocks
   - Anthropic rejects: "unexpected tool_use_id found in tool_result blocks"

### Existing PRs (NOT MERGED)
- **PR #161**: `deduplicate_tools()` - removes duplicate tool names
- **PR #167**: `prune_orphaned_tool_results()` - removes orphaned tool results

### Proposed Fix
Merge PRs #161 and #167 to main.

---

## Issue 5: Thinking Blocks (ALREADY FIXED)

The worktree-purpose mentions "Thinking blocks stripped from non-streaming responses" but this appears to be **already fixed** via merged PRs:
- PR #131: Preserve thinking blocks in non-streaming responses (MERGED)
- PR #134: Handle thinking blocks in streaming responses (MERGED)
- PR #138: Use dedicated thinking_blocks field (MERGED)

Issue #128 is CLOSED.

---

## Test Results Summary

### After Docker Fix

**test_gateway_matrix.py**: 7 passed, 1 failed
- Failed: `test_anthropic_client_openai_backend_non_streaming` (cross-format routing)

**test_extra_params.py**: 14 passed, 2 failed
- Failed: `test_anthropic_metadata_parameter_accepted` (metadata API change)
- Failed: `test_anthropic_client_to_openai_backend_with_extra_params` (metadata + cross-format)

---

## Recommended Next Steps

1. **Commit docker-compose.yaml fix** - Already done in this branch

2. **Update metadata tests** - Change custom metadata fields to use only `user_id`

3. **Handle cross-format tests** - Either skip or implement Phase 2 model-based routing

4. **Merge /compact fixes** - Review and merge PRs #161 and #167

5. **Verify thinking blocks** - If still seeing issues, may need to bisect for regression
