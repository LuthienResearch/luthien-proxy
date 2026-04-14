# Policies Guide

## Scope

- This directory contains concrete policy implementations and reusable presets.
- Read `src/luthien_proxy/policy_core/` and `ARCHITECTURE.md` first if you are unsure which hooks a policy owns.

## Choose the right base class

| Base | Use it when | Tradeoff |
| --- | --- | --- |
| `TextModifierPolicy` | you only transform text content | simplest, preserves streaming semantics |
| `SimplePolicy` | you need complete content/tool calls before deciding | buffers streaming blocks |
| direct Anthropic hooks | you need event-level control | most power, easiest to break SSE |
| `MultiSerialPolicy` | you are composing existing policies | ordering matters and is left-to-right |

## Local rules

- Policies are singleton objects created at load time; never keep request-specific mutable state on the instance.
- Use `PolicyContext.get_request_state(...)` for per-request state.
- Config-time collections must be immutable because `freeze_configured_state()` rejects mutable containers.
- Keep policy behavior Anthropic-native; this gateway no longer has an OpenAI-format execution path.

## Composition rules

- `MultiSerialPolicy` applies request, response, and stream hooks in list order.
- Downstream policies see upstream modifications.
- If a composed policy emits stream-complete events, downstream stream-event hooks still run on those emissions.
- Prefer composition over duplicating policy logic when behavior can be layered cleanly.

## Common traps

- Picking `SimplePolicy` for work that really needs event-by-event streaming decisions.
- Implementing direct stream hooks and accidentally emitting malformed Anthropic event sequences.
- Hardcoding credentials or auth assumptions inside policies instead of using `PolicyContext` / auth providers.
- Re-implementing helper logic already present in `simple_llm_utils.py`, `tool_call_judge_utils.py`, or `multi_policy_utils.py`.

## Verification targets

- Unit tests for policy logic and config validation.
- Use the admin/policy-set flow or policy test helpers for end-to-end activation.
- Use `mock_e2e` for policies that alter streaming or tool-call event flow.
- Use `sqlite_e2e` / integration coverage when the policy writes to DB tables such as judge decisions or debug logs.
