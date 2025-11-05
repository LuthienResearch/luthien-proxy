# Hardcoded Constants Audit Report

**Date:** 2025-11-05
**Audited Areas:** `src/luthien_proxy/v2/` (all subdirectories)

## Summary

Three specialized agents audited the codebase and found **15+ categories** of hardcoded constants that should be configurable. The most critical issue is a **global side effect** in the policy code that affects the entire application.

## Critical Issues (Fix Immediately)

### 1. Global `litellm.drop_params` Setting ✅ FIXED

**Location:** `src/luthien_proxy/v2/policies/tool_call_judge_policy.py:57`

**Was:**

```python
# TODO: MOVE THIS SOMEWHERE ELSE!
# IT SHOULDN'T BE SET GLOBALLY IN A POLICY FILE. WTF.
litellm.drop_params = True
```

**Problem:** Sets a global flag that affects ALL litellm calls in the application, just by importing the policy.

**Fixed:** Moved to application initialization in [main.py:55](src/luthien_proxy/v2/main.py#L55):

```python
# Configure litellm globally (moved from policy file to prevent import side effects)
litellm.drop_params = True
logger.info("Configured litellm: drop_params=True")
```

---

## High Priority Issues

### 2. Hardcoded Default Model in AnthropicClientFormatter ✅ FIXED

**Location:** `src/luthien_proxy/v2/streaming/client_formatter/anthropic.py:19`

**Was:**

```python
def __init__(self, model_name: str = "claude-3-opus-20240229"):
```

**Fixed:** Now accepts `model_name` from the request:

```python
client_formatter = AnthropicClientFormatter(model_name=request_message.model)
```

### 3. Hardcoded Stream Timeout
**Location:** `src/luthien_proxy/v2/streaming/streaming_orchestrator.py:83`

```python
timeout_seconds: float = 30.0  # Hardcoded default
```

**Impact:** May be too short for slow LLMs or too long for fast responses.

**Recommendation:** Add to `v2_config.yaml`:
```yaml
streaming:
  default_timeout_seconds: 30.0
```

### 4. Unbounded Queue Sizes
**Locations:**
- `src/luthien_proxy/v2/streaming/streaming_orchestrator.py:110-111`
- `src/luthien_proxy/v2/streaming/policy_executor/executor.py:103`
- `src/luthien_proxy/v2/orchestration/policy_orchestrator.py:44` (defaults to 10000)

**Problem:** Can lead to memory exhaustion if producer is faster than consumer.

**Recommendation:** Add configurable limits with sane defaults.

### 5. Model Capability Detection Logic
**Location:** `src/luthien_proxy/v2/policies/tool_call_judge_policy.py:460-464`

```python
if "gpt-4o" in model_lower or "gpt-4-turbo" in model_lower or "gpt-3.5-turbo" in model_lower:
    kwargs["response_format"] = {"type": "json_object"}
```

**Problem:** Brittle string matching that will break with new models.

**Recommendation:** Create a configurable list of JSON-capable models or use try/except with fallback.

---

## Medium Priority Issues

### 6. Default Judge Model
**Location:** `src/luthien_proxy/v2/policies/tool_call_judge_policy.py:86`

```python
model: str = "openai/gpt-4"
```

**Problem:** Defaults to expensive GPT-4. Already configurable via YAML, but default should be more affordable.

**Recommendation:** Change default to `"openai/gpt-3.5-turbo"` or make it required (no default).

### 7. Server Host/Port
**Location:** `src/luthien_proxy/v2/main.py:169`

```python
uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
```

**Recommendation:**
```python
host = os.getenv("SERVER_HOST", "0.0.0.0")
port = int(os.getenv("SERVER_PORT", "8000"))
uvicorn.run(app, host=host, port=port, log_level="info")
```

### 8. Redis Channel Names
**Locations:**
- `src/luthien_proxy/v2/observability/redis_event_publisher.py:29`
- `src/luthien_proxy/v2/storage/persistence.py:392`

```python
V2_ACTIVITY_CHANNEL = "luthien:activity"
channel = f"luthien:conversation:{event.call_id}"
```

**Problem:** Hardcoded "luthien:" prefix could conflict in multi-tenant deployments.

**Recommendation:**
```python
REDIS_NAMESPACE = os.getenv("REDIS_NAMESPACE", "luthien")
V2_ACTIVITY_CHANNEL = f"{REDIS_NAMESPACE}:activity"
```

### 9. Argument Truncation Lengths
**Location:** `src/luthien_proxy/v2/policies/tool_call_judge_policy.py:355, 365, 376, 388`

```python
arguments[:200]  # For logging
arguments[:150]  # For error messages
```

**Recommendation:** Extract to class constants or config parameters.

### 10. Security Message Templates
**Location:** `src/luthien_proxy/v2/policies/tool_call_judge_policy.py:365-389`

Multiple hardcoded security/error messages with emojis (🚨, ⚠️).

**Recommendation:** Make templates configurable, especially for organizations with different message requirements.

---

## Low Priority Issues

### 11. Event Names
All policies have hardcoded event names like:
- `"policy.judge.evaluation_started"`
- `"policy.all_caps.content_transformed"`

**Recommendation:** Consider making event prefix configurable for organizational standards.

### 12. ID Prefixes
- Tool call IDs: `f"tool_{index}"`
- Message IDs: `f"msg_{transaction_id}"`
- Chunk IDs: `"chatcmpl-generated"`

**Recommendation:** Extract to constants or make configurable.

### 13. Zero Timestamps
**Location:** `src/luthien_proxy/v2/streaming/utils.py:52, 105`

```python
created=0  # Should be current timestamp
```

**Fix:**
```python
import time
created=int(time.time())
```

### 14. SSE Format Strings
Hardcoded SSE format in multiple files:
```python
f"event: {event_type}\ndata: {json.dumps(event)}\n\n"
```

**Recommendation:** Extract to helper functions for consistency.

### 15. Heartbeat Intervals
**Location:** `src/luthien_proxy/v2/observability/redis_event_publisher.py:157-158`

```python
heartbeat_seconds: float = 15.0
timeout_seconds: float = 1.0
```

**Already has parameters, but could use env var defaults.**

---

## Recommended Configuration Structure

Create a centralized defaults module:

```python
# src/luthien_proxy/v2/defaults.py

import os
from typing import Any

class V2Config:
    """Central configuration with environment overrides."""

    # Streaming
    STREAM_TIMEOUT_SECONDS: float = float(os.getenv("STREAM_TIMEOUT_SECONDS", "30.0"))
    PIPELINE_QUEUE_SIZE: int = int(os.getenv("PIPELINE_QUEUE_SIZE", "10000"))

    # Redis
    REDIS_NAMESPACE: str = os.getenv("REDIS_NAMESPACE", "luthien")

    # Server
    SERVER_HOST: str = os.getenv("SERVER_HOST", "0.0.0.0")
    SERVER_PORT: int = int(os.getenv("SERVER_PORT", "8000"))

    # Observability
    GRAFANA_URL: str = os.getenv("GRAFANA_URL", "http://localhost:3000")
    SSE_HEARTBEAT_SECONDS: float = float(os.getenv("SSE_HEARTBEAT_SECONDS", "15.0"))
```

## Files Requiring Changes

**Critical:**
- `src/luthien_proxy/v2/policies/tool_call_judge_policy.py` (global side effect)

**High Priority:**
- `src/luthien_proxy/v2/streaming/streaming_orchestrator.py` (timeout)
- `src/luthien_proxy/v2/streaming/policy_executor/executor.py` (queue size)
- `src/luthien_proxy/v2/orchestration/policy_orchestrator.py` (queue size)

**Medium Priority:**
- `src/luthien_proxy/v2/main.py` (server config)
- `src/luthien_proxy/v2/observability/redis_event_publisher.py` (channel names)
- `src/luthien_proxy/v2/storage/persistence.py` (channel names)

## Next Steps

1. **Immediate:** Fix the global `litellm.drop_params` side effect
2. **Short-term:** Add timeout and queue size configuration to `v2_config.yaml`
3. **Medium-term:** Create centralized defaults module
4. **Long-term:** Refactor model capability detection to use config-driven approach
