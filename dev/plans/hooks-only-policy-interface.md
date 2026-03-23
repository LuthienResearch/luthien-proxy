# Plan: Unify Policy Interface to Hooks-Only

**Date**: 2026-03-23
**Triggered by**: PR #409 — OnboardingPolicy hooks silently broke in MultiSerialPolicy
because `run_anthropic` and hooks are separate code paths that must be kept in sync manually.

## Problem

Policies currently have two execution models:

1. **`run_anthropic(io, context)`** — "I own execution." The policy gets an IO object,
   calls the backend itself, yields emissions. Used when a policy runs standalone.

2. **Hook methods** (`on_anthropic_request`, `on_anthropic_response`,
   `on_anthropic_stream_event`, `on_anthropic_stream_complete`) — "I'm a step in a
   pipeline." MultiSerialPolicy calls these to chain policies.

These are **completely separate code paths**. Nothing enforces consistency between them.
A policy can implement `run_anthropic` correctly while its hooks are broken (PR #409),
or vice versa. The hooks aren't even on the formal protocol — they're discovered via
`getattr()`.

Every policy that needs request data in response hooks must independently invent a
stashing pattern (`_OnboardingState`, `_StreamState`, `_StreamBufferState`). This is
framework plumbing that each policy reinvents.

## Current State

| Category | Count | Details |
|---|---|---|
| Policies using hooks exclusively (via AnthropicHookPolicy) | 7 | SimplePolicy, StringReplacement, ToolCallJudge, SimpleLLM, DebugLogging, DogfoodSafety, NoOp |
| Policies with custom `run_anthropic` that duplicates hook logic | 2 | TextModifierPolicy, OnboardingPolicy |
| Orchestrators with custom `run_anthropic` | 2 | MultiSerialPolicy, MultiParallelPolicy |
| Call sites that invoke `run_anthropic` | 1 | `anthropic_processor._execute_anthropic_policy()` |

The hook model is already dominant. `AnthropicHookPolicy.run_anthropic` is just
"call hooks around a backend call" — that's framework orchestration, not policy logic.

## Proposed Change

### 1. Drop MultiParallelPolicy

It's the only policy that needs `run_anthropic`'s full power (multiple backend calls).
It's unused in any config. Remove it entirely to unblock the interface simplification.

**Files to delete:**
- `src/luthien_proxy/policies/multi_parallel_policy.py`
- `tests/unit_tests/policies/test_multi_parallel_policy.py`
- `tests/e2e_tests/test_mock_multi_parallel.py`

**Files to update:**
- `src/luthien_proxy/policies/__init__.py` — remove export
- `src/luthien_proxy/static/policy_config.js` — remove from UI policy list
- `tests/unit_tests/admin/test_policy_discovery.py` — remove test cases
- `docs/policies.md`, `README.md`, `CHANGELOG.md` — remove references

### 2. Make hooks the formal protocol

Replace `AnthropicExecutionInterface` (which defines only `run_anthropic`) with a
protocol that defines the four hook methods:

```python
@runtime_checkable
class AnthropicExecutionInterface(Protocol):
    async def on_anthropic_request(
        self, request: AnthropicRequest, context: PolicyContext
    ) -> AnthropicRequest: ...

    async def on_anthropic_response(
        self, response: AnthropicResponse, context: PolicyContext
    ) -> AnthropicResponse: ...

    async def on_anthropic_stream_event(
        self, event: MessageStreamEvent, context: PolicyContext
    ) -> list[MessageStreamEvent]: ...

    async def on_anthropic_stream_complete(
        self, context: PolicyContext
    ) -> list[AnthropicPolicyEmission]: ...
```

### 3. Move execution orchestration to the executor

`anthropic_processor._execute_anthropic_policy()` directly implements the hook loop
instead of delegating to `policy.run_anthropic()`:

```python
async def _execute_anthropic_policy(policy, io, ctx):
    request = await policy.on_anthropic_request(io.request, ctx)
    io.set_request(request)

    if request.get("stream", False):
        async for event in io.stream(request):
            for e in await policy.on_anthropic_stream_event(event, ctx):
                yield e
        for e in await policy.on_anthropic_stream_complete(ctx):
            yield e
    else:
        response = await io.complete(request)
        yield await policy.on_anthropic_response(response, ctx)
```

### 4. Delete `run_anthropic` from all policies

- **AnthropicHookPolicy**: delete `run_anthropic` (it was just the hook loop). May be
  able to merge remaining base functionality into `BasePolicy` or remove entirely if
  the only value was providing the default `run_anthropic`.
- **TextModifierPolicy**: delete `run_anthropic`. Its hooks already handle streaming
  text modification and suffix injection. Net deletion of ~70 lines of duplicate logic.
- **OnboardingPolicy**: delete `run_anthropic` and `_passthrough`. The hooks + base
  class hooks handle everything. This also eliminates the need for the `_OnboardingState`
  stashing pattern entirely — the executor passes the request to `on_anthropic_request`,
  and subsequent hooks just work.
- **MultiSerialPolicy**: delete `run_anthropic` and `_passthrough_anthropic`. Its hook
  methods already chain sub-policy hooks correctly.

### 5. Simplify MultiSerialPolicy

With hooks as the formal interface, MultiSerialPolicy no longer needs `getattr()` to
discover hooks. It calls them directly on the typed protocol. The `_validate_interface`
calls can use `isinstance` checks against the protocol.

## What This Eliminates

- **The "two code paths" bug class** (PR #409): impossible, because there's only one path.
- **Per-policy state stashing boilerplate**: the executor passes the request to
  `on_anthropic_request`, so hooks naturally have access to it. Policies that need
  request data in response hooks still use `get_request_state()`, but the framework
  handles the common case.
- **Duck-typed hook discovery**: `getattr(policy, "on_anthropic_stream_complete", None)`
  replaced by protocol-typed method calls.
- **~200 lines of duplicate streaming logic** across TextModifierPolicy and
  AnthropicHookPolicy `run_anthropic` implementations.

## Migration Order

1. Drop MultiParallelPolicy (standalone PR, no dependencies)
2. Formalize hooks on the protocol
3. Move execution loop to the executor
4. Delete `run_anthropic` from all policies
5. Clean up: merge/remove AnthropicHookPolicy if empty

Steps 2-4 can be one PR. Step 1 should be separate since it's a removal.

## Risks

- **TextModifierPolicy's `run_anthropic` has subtle streaming logic** (suffix injection,
  held stops, tool_use interleaving). Its hook methods were written to replicate this.
  Need to verify the hooks handle all edge cases before deleting `run_anthropic`.
  The existing test suite for TextModifierPolicy should cover this — run both the
  `run_anthropic` tests and hook tests, then delete `run_anthropic` and verify the
  hook tests still pass.
- **External policy authors** who implemented `run_anthropic` directly would need to
  migrate to hooks. Since we don't have external policy authors yet, this is free.
