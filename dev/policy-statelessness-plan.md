# Policy Statelessness: Findings and Recommendations

**Date:** 2026-03-04
**Context:** Recurring issue where contributors (human and AI) keep putting request-scoped state on or near policy objects. Policies are long-lived singletons shared across concurrent requests and must be stateless.

## The Invariant

> Policy instances are created once at configuration time and shared across all concurrent requests. They must never hold request-scoped state. All per-request data lives on `PolicyContext` (shared across the request lifecycle) or `AnthropicPolicyIO` / the IO protocol (request-scoped I/O surface).

This invariant is **not documented anywhere**. That's the root cause.

---

## Findings

### 1. `get_policy_state(self, ...)` trains the wrong mental model

**Location:** `src/luthien_proxy/policy_core/policy_context.py:173-208`

```python
def get_policy_state(self, owner: object, expected_type: type[T], factory: Callable[[], T]) -> T:
    key = (id(owner), expected_type)
    ...
```

The API takes `self` (the policy instance) as the owner key. Even though the state is stored on `PolicyContext`, the call site reads as "give me **my** state":

```python
# From simple_policy.py:98-100
def _anthropic_state(self, context: "PolicyContext") -> _SimplePolicyAnthropicState:
    return context.get_policy_state(self, _SimplePolicyAnthropicState, _SimplePolicyAnthropicState)
```

**Used by:** `SimplePolicy`, `DogfoodSafetyPolicy`, `ToolCallJudgePolicy`

**Problem:** The naming and `self`-keying create an ownership illusion. The method name says "policy state" not "request state keyed by policy type."

### 2. `scratchpad` is an untyped shared mutable bag

**Location:** `src/luthien_proxy/policy_core/policy_context.py:74, 95-106`

```python
self._scratchpad: dict[str, Any] = {}
```

Docstring says: "Policies can use the scratchpad to: Track whether safety checks have been performed, Store intermediate results from trusted monitors, Accumulate metrics across streaming chunks."

**Problem:** A `dict[str, Any]` with no schema is a magnet for ad-hoc state. Any time someone needs to pass data between request phases, the scratchpad is the path of least resistance. There's no typing, no collision prevention, no visibility into what's in there.

### 3. `_AnthropicPolicyIO` mixes infrastructure state with policy-facing surface

**Location:** `src/luthien_proxy/pipeline/anthropic_processor.py:78-223`

The IO object already holds mutable request-scoped state:
- `_request` (mutated via `set_request()`)
- `_first_backend_response` (accumulated during execution)
- `_request_recorded` (flag to prevent duplicate recording)
- `_backend_headers` / `_extra_headers` (forwarded to upstream)

When someone needs to add request-scoped data (like backend headers), the IO object is the obvious place because it's already a state bag. This is actually the **correct** place for request-scoped IO state — but there's no documentation distinguishing "IO infrastructure state" from "stuff policies should interact with via the protocol."

### 4. `freeze_configured_state()` exempts private attrs

**Location:** `src/luthien_proxy/policy_core/base_policy.py:30-55`

```python
def _validate_no_mutable_instance_state(self) -> None:
    for attr_name, value in vars(self).items():
        if attr_name.startswith("_"):
            continue  # <-- private attrs are exempt
        if isinstance(value, mutable_types):
            raise TypeError(...)
```

The guard catches `self.my_list = []` but not `self._my_list = []`. The comment says "Private attrs are treated as internal implementation details." This reads as permission to use private attrs for mutable state on the policy.

### 5. No explicit statelessness documentation

Neither `CLAUDE.md`, `BasePolicy` docstrings, nor any policy interface docstrings state the invariant. The closest thing is the error message in `freeze_configured_state`: "keep request state in PolicyContext." But that's a runtime error message, not a documented contract.

---

## Who gets confused and why

| Signal | What it communicates |
|--------|---------------------|
| `get_policy_state(self, ...)` | "Policies own state, just store it on the context" |
| `scratchpad: dict[str, Any]` | "Throw anything you need in here" |
| `_AnthropicPolicyIO` holding `_request`, `_first_backend_response` | "Request-scoped objects hold mutable state, add more" |
| `freeze_configured_state` skipping `_private` | "Private mutable attrs on policies are fine" |
| No documented invariant | "I guess I'll figure it out from the code" |

---

## Recommendations

### A. Document the invariant (do first, low risk)

1. **Add to `CLAUDE.md`** under a "Policy Architecture" section:
   - Policies are singletons, stateless, shared across concurrent requests
   - Request-scoped state goes on `PolicyContext` or the IO object
   - The IO protocol is the request-scoped counterpart to the stateless policy

2. **Add to `BasePolicy` class docstring:**
   - Explicit "Policies must be stateless" statement
   - Reference to where request state should live

3. **Add to `AnthropicPolicyIOProtocol` docstring:**
   - "This is the request-scoped stateful counterpart to a stateless policy"

### B. Rename `get_policy_state` → `get_request_state` (medium risk)

The method is on `PolicyContext` which is request-scoped. The state it holds is request-scoped. The name should reflect that.

```python
# Before
context.get_policy_state(self, _SimplePolicyAnthropicState, _SimplePolicyAnthropicState)

# After
context.get_request_state(self, _SimplePolicyAnthropicState, _SimplePolicyAnthropicState)
```

Similarly: `pop_policy_state` → `pop_request_state`, `_policy_state` → `_request_state`.

**Callers to update:**
- `simple_policy.py` (2 calls)
- `dogfood_safety_policy.py` (4 calls)
- `tool_call_judge_policy.py` (4 calls)
- `policy_context.py` (internal references)
- Tests for all of the above

### C. Tighten `freeze_configured_state` (low risk)

Remove the private-attr exemption, or at least warn on private mutable attrs. If a policy legitimately needs a private mutable attr at config time (e.g., a compiled regex cache), it can use `@dataclass(frozen=True)` or a tuple.

**Risk:** Need to audit existing policies for private mutable attrs that are actually config-time state (not request-scoped). Quick grep shows policies use `self._config` (a frozen Pydantic model) and `self._sub_policies` (list, but set once at init) — these would need adjustment.

### D. Consider removing `scratchpad` (higher risk, longer term)

Replace with typed state via `get_request_state()`. The scratchpad's untyped nature means there's no IDE support, no collision detection, and no way to know what's in there without reading every policy.

**Current scratchpad usage:** Check if anything actually uses it — it may already be dead code replaced by `get_policy_state`.

### E. Separate IO protocol into read-only and mutable surfaces (longer term)

Split `AnthropicPolicyIOProtocol` into:
- **Read-only surface:** `request`, `first_backend_response`, `backend_headers` (for inspection)
- **Mutation methods:** `set_request()`, `complete()`, `stream()`

This makes it clear what policies can read vs. what they can change. Currently everything is on one flat protocol.

---

## Suggested execution order

1. **A** (docs) — immediate, zero code risk
2. **B** (rename) — straightforward mechanical refactor, good first PR
3. **C** (freeze tightening) — small but needs audit of existing private attrs
4. **D** (scratchpad) — only if scratchpad is actually unused; check first
5. **E** (IO split) — larger design change, separate planning needed

---

## Files to change

### For recommendations A + B + C:

**Core:**
- `src/luthien_proxy/policy_core/policy_context.py` — rename methods, update docstrings
- `src/luthien_proxy/policy_core/base_policy.py` — tighten freeze, update docstring
- `src/luthien_proxy/policy_core/anthropic_execution_interface.py` — update docstring

**Policies (mechanical rename):**
- `src/luthien_proxy/policies/simple_policy.py`
- `src/luthien_proxy/policies/dogfood_safety_policy.py`
- `src/luthien_proxy/policies/tool_call_judge_policy.py`

**Tests:**
- `tests/unit_tests/policies/test_simple_policy.py`
- `tests/unit_tests/policies/test_dogfood_safety_policy.py`
- `tests/unit_tests/policies/test_tool_call_judge_policy.py`
- `tests/unit_tests/policy_core/test_policy_context.py`
- Any test referencing `get_policy_state` or `pop_policy_state`

**Docs:**
- `CLAUDE.md` — add Policy Architecture section
