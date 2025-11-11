# V2 Module Naming & Organization Refactoring

**Date:** 2025-10-21
**Status:** Proposed (awaiting approval)
**Scope:** File and class naming alignment, improved code organization, architectural clarifications

---

## Executive Summary

The V2 architecture is sound, but naming obscures relationships and intent. This document proposes systematic renames following a clear principle:

**Core Principle:** When a module defines a single class (or that class is the overridingly most important thing), the module name should match the class name.

This aligns Python best practices and makes code intent immediately obvious to readers.

---

## Current Problems & Their Fixes

### Problem 1: `control/local.py` + `ControlPlaneLocal`

**Issues:**
- "Local" is ambiguous. Does it mean "local to this machine"? "Local scope"? "Localized"?
- Doesn't convey that this is an **in-process, synchronous** implementation
- Filename doesn't match class name

**Fix:**
- Rename class: `ControlPlaneLocal` → `SynchronousControlPlane`
- Rename file: `control/local.py` → `control/synchronous_control_plane.py`
- Docstring updates to clarify: "Synchronous in-process control plane implementation"

**Rationale:**
- "Synchronous" clarifies the execution model (vs. async remote calls)
- Pairs well with future `AsynchronousControlPlane` or `RemoteControlPlane`
- Filename matches class name

---

### Problem 2: `control/streaming.py` + `StreamingOrchestrator`

**Issues:**
- Filename doesn't indicate the class is an orchestrator
- Generic name makes relationship unclear

**Fix:**
- Rename file: `control/streaming.py` → `control/streaming_orchestrator.py`
- Keep class: `StreamingOrchestrator` (already correct)

**Rationale:**
- Filename matches class name
- Clearer that this is a queue orchestrator, not a generic streaming module

---

### Problem 3: `control/interface.py` + `ControlPlaneService`

**Issues:**
- "Interface" is vague; this is a **Protocol** (in Python typing terms)
- Filename doesn't match class name
- Naming suggests it's generic, but it's actually specific to control plane

**Fix:**
- Rename file: `control/interface.py` → `control/control_plane_protocol.py`
- Rename class: `ControlPlaneProtocol`

**Rationale:**
- Filename matches class name
- Reduces confusion with other interface files

---

### Problem 4: `policies/context.py` + `PolicyContext`

**Issues:**
- "PolicyContext" is too generic; doesn't indicate it's **per-call execution state**
- Name doesn't clarify that this is used across ALL policy methods (request, full response, streaming response)
- "Context" alone is ambiguous - there are many kinds of contexts
- Filename doesn't match class name

**Fix:**
- Rename class: `PolicyContext` → `PolicyCallContext`
- Rename file: `policies/context.py` → `policies/policy_call_context.py`
- Update docstring to clarify it carries per-call state (call_id, OTel span, event publisher) across all policy methods

**Rationale:**
- "PolicyCallContext" indicates this is context for a single LLM call (request/response cycle)
- "Call" better represents the full lifecycle than "Request" (which would be misleading - this is used for responses too)
- Stays policy-scoped while being more specific
- Filename matches class name

---

### Problem 5: `observability/bridge.py` + `SimpleEventPublisher`

**Issues:**
- "Bridge" is vague; what kind of bridge?
- "Simple" is misleading and not evergreen (today's "simple" is tomorrow's outdated terminology)
- Filename doesn't match class name
- Obscures that this is specifically a **Redis pub/sub publisher**

**Fix:**
- Rename class: `SimpleEventPublisher` → `RedisEventPublisher`
- Rename file: `observability/bridge.py` → `observability/redis_event_publisher.py`
- Also rename function: `stream_activity_events()` → `stream_activity_events_sse()` (clarifies Server-Sent Events format)

**Rationale:**
- "Redis" specificity is clear and maintainable
- Filename matches class name
- Distinguishes from potential future `PostgresEventPublisher` or `WebhookEventPublisher`
- Removes temporal language ("simple")

---

### Problem 6: `queue_utils.py` at v2 root

**Issues:**
- Utility functions at the root suggest they're generally useful
- Actually only used by `StreamingOrchestrator`
- Location doesn't clarify ownership

**Fix:**
- Move: `queue_utils.py` → `control/queue_utils.py`
- Update imports in `control/streaming_orchestrator.py`

**Rationale:**
- Clarifies these are streaming utilities
- Reduces v2 root clutter
- Makes the relationship to orchestrator explicit

---

### Problem 7: `llm/format_converters.py`

**Issues:**
- "Format conversion" is not **LLM-provider-specific**; it's about normalizing requests/responses to OpenAI format
- Placed in `llm/` package suggests it's part of LLM integration
- Actually part of the core gateway pipeline (request normalization)
- If only module in `llm/`, package hierarchy is premature

**Fix - Option A (Minimal):**
- Rename file: `llm/format_converters.py` → `llm/format_converter.py` (singular)
- Class name: keep as-is or rename main class to `FormatConverter`
- Rationale: If `llm/` package has other provider integrations in future, this works. Otherwise, low risk.

**Fix - Option B (Refactor - Deferred):**
- Move to `api/format_converters.py` or `gateway/format_converters.py`
- Rationale: These functions are part of API-level normalization, not LLM-specific logic. Deferred pending clearer API package structure.

**Recommendation:** Option A for now. Revisit when more LLM provider integrations are planned.

---

### Problem 8: `control/models.py`

**Status:** ✓ No change needed

**Rationale:**
- Contains multiple domain models (`StreamingError`, `StreamingContext`, potentially `StreamingMetrics`)
- "Models" accurately describes a collection
- Doesn't define a single primary class

---

### Problem 9: `storage/events.py`

**Status:** ✓ No change needed

**Rationale:**
- Utility module for event emission (`emit_request_event()`, `emit_response_event()`, `reconstruct_full_response_from_chunks()`)
- Multiple exports; not a single-class module
- "Events" accurately describes the concern

**Note:** Consider renaming functions to be more explicit about what they do:
- `emit_request_event()` ✓ (already clear)
- `emit_response_event()` ✓ (already clear)
- `reconstruct_full_response_from_chunks()` → consider renaming to `merge_streaming_chunks_into_response()` or `reconstruct_response_from_streaming()` (future refinement)

---

### Problem 10: `telemetry.py`

**Status:** ✓ No change needed

**Rationale:**
- Collection of telemetry utilities and initialization functions
- Not a single-class module
- "Telemetry" accurately describes the concern

---

### Problem 11: `debug/routes.py`

**Status:** ✓ No change needed

**Rationale:**
- Collection of route handlers and response models
- Not a single-class module
- "Routes" accurately describes the concern

---

### Problem 12: `messages.py` (v2 root)

**Status:** ✓ No change needed

**Rationale:**
- Defines `Request` class (OpenAI-normalized request model)
- While it's a single class, it's API-level and closely tied to versioning
- Placing at v2 root makes sense for API versioning clarity
- Could move to `api/request.py` later if API versioning becomes explicit

---

## Architectural Clarifications (Deferred)

These are good-to-have but don't block the current refactoring:

### 1. `PolicyCallContext` Long-Term Home

Currently proposed: Stay in `policies/policy_call_context.py`

Future consideration: If more core infrastructure accumulates (shared between policies, control plane, observability), create `core/` package:
```
v2/
├── core/
│   ├── policy_call_context.py
│   ├── format_converters.py
│   └── tracing.py (extracted from telemetry.py)
├── control/
├── policies/
├── observability/
└── storage/
```

**Decision:** Defer. Proceed with renaming in `policies/` for now.

### 2. API-Level Concerns Package

Related: `messages.py` could move to `api/messages.py` if you want to organize API-level concerns:
```
v2/
├── api/
│   ├── messages.py
│   ├── format_converters.py (moved from llm/)
│   └── routes.py (if consolidated)
```

**Decision:** Defer. Keep current structure; revisit when API package structure becomes clearer.

---

## Affected Files & Import Updates

### Files Requiring Changes

1. **`src/luthien_proxy/v2/__init__.py`**
   - Update exports for all renamed modules and classes

2. **`src/luthien_proxy/v2/main.py`**
   - `from luthien_proxy.control.local import ControlPlaneLocal`
     → `from luthien_proxy.control.synchronous_control_plane import SynchronousControlPlane`
   - `ControlPlaneLocal(...)` → `SynchronousControlPlane(...)`
   - Update observability imports for `RedisEventPublisher`

3. **`src/luthien_proxy/v2/control/__init__.py`**
   - Update all exports

4. **`src/luthien_proxy/v2/control/synchronous_control_plane.py`** (renamed from `local.py`)
   - `from luthien_proxy.control.streaming import StreamingOrchestrator`
     → `from luthien_proxy.control.streaming_orchestrator import StreamingOrchestrator`
   - `from luthien_proxy.policies.context import PolicyContext`
     → `from luthien_proxy.policies.policy_call_context import PolicyCallContext`
   - Update all class references and documentation

5. **`src/luthien_proxy/v2/control/streaming_orchestrator.py`** (renamed from `streaming.py`)
   - `from luthien_proxy.queue_utils import get_available`
     → `from luthien_proxy.control.queue_utils import get_available`

6. **`src/luthien_proxy/v2/policies/base.py`**
   - `from luthien_proxy.policies.context import PolicyContext`
     → `from luthien_proxy.policies.policy_call_context import PolicyCallContext`
   - Update all method signatures and docstrings
   - Update `async def process_request(self, request: Request, context: PolicyCallContext)`

7. **`src/luthien_proxy/v2/policies/noop.py`**
   - Update imports and references to `PolicyCallContext`
   - Update method signatures

8. **`src/luthien_proxy/v2/policies/uppercase_nth_word.py`**
   - Update imports and references to `PolicyCallContext`
   - Update method signatures

9. **`src/luthien_proxy/v2/observability/__init__.py`**
   - Update exports: `from .redis_event_publisher import RedisEventPublisher, stream_activity_events_sse`
   - Remove old bridge exports

10. **Test Files:**
    - `tests/unit_tests/v2/test_control_local.py`
      - Rename to `tests/unit_tests/v2/test_synchronous_control_plane.py`
      - Update all imports and class references
      - Update test method comments for clarity
    - Any other v2 tests importing renamed modules
    - Check `conftest.py` for fixtures using old names

---

## Complete Import Reference (Old → New)

```python
# Control plane implementations
from luthien_proxy.control.local import ControlPlaneLocal
→ from luthien_proxy.control.synchronous_control_plane import SynchronousControlPlane

# Control plane protocol definition
from luthien_proxy.control.interface import ControlPlaneService
→ from luthien_proxy.control.control_plane_protocol import ControlPlaneProtocol

# Streaming orchestrator
from luthien_proxy.control.streaming import StreamingOrchestrator
→ from luthien_proxy.control.streaming_orchestrator import StreamingOrchestrator

# Queue utilities
from luthien_proxy.queue_utils import get_available
→ from luthien_proxy.control.queue_utils import get_available

# Policy call context (used across all policy methods)
from luthien_proxy.policies.context import PolicyContext
→ from luthien_proxy.policies.policy_call_context import PolicyCallContext

# Real-time event publisher
from luthien_proxy.observability.bridge import SimpleEventPublisher
→ from luthien_proxy.observability.redis_event_publisher import RedisEventPublisher

# Activity streaming (Server-Sent Events)
from luthien_proxy.observability.bridge import stream_activity_events
→ from luthien_proxy.observability.redis_event_publisher import stream_activity_events_sse
```

---

## Implementation Plan

### Phase 1: Low-Risk File Renames (No Class Changes)
1. Rename `control/streaming.py` → `control/streaming_orchestrator.py`
2. Move `queue_utils.py` → `control/queue_utils.py`
3. Update imports in affected files
4. Run tests to verify

**Estimated effort:** ~10 min changes, ~5 min testing

### Phase 2: Breaking Changes (Class Renames + File Moves)
1. Rename `ControlPlaneService` → `ControlPlaneProtocol`
2. Rename and move `control/interface.py` → `control/control_plane_protocol.py`
3. Rename `ControlPlaneLocal` → `SynchronousControlPlane`
4. Rename and move `control/local.py` → `control/synchronous_control_plane.py`
5. Rename `SimpleEventPublisher` → `RedisEventPublisher`
6. Rename and move `observability/bridge.py` → `observability/redis_event_publisher.py`
7. Update all imports and references
8. Rename test file: `test_control_local.py` → `test_synchronous_control_plane.py`
9. Run tests to verify

**Estimated effort:** ~35 min changes, ~10 min testing

### Phase 3: Context Refactoring (Most Widespread)
1. Rename `PolicyContext` → `PolicyCallContext`
2. Rename and move `policies/context.py` → `policies/policy_call_context.py`
3. Update all policy implementations (base, noop, uppercase_nth_word)
4. Update control plane (synchronous_control_plane.py)
5. Update all tests
6. Run full test suite

**Estimated effort:** ~45 min changes, ~15 min testing

---

## Benefits

- **Clarity:** Filename immediately indicates class name and purpose
- **Discoverability:** Grep/IDE search for `SynchronousControlPlane` finds both file and class
- **Maintainability:** New contributors understand module intent without reading code
- **Consistency:** Aligns with Python best practices and existing project conventions
- **Future-proofing:** Removes temporal language ("simple", "local") that becomes outdated

---

## Rollback Plan

If issues arise during implementation:
- Git history preserves old names; can revert individual commits
- Since this is on feature branch `integrated-architecture`, no impact to main
- Each phase is independent; can pause and review after each phase
