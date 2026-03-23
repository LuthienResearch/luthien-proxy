---
category: Fixes
pr: 409
---

**OnboardingPolicy in MultiSerialPolicy chains**: Fixed two bugs preventing proper composition.
  - OnboardingPolicy hook methods silently failed because `context.request` is always `None` in the Anthropic path. Now stashes the request via `get_request_state()`.
  - `MultiSerialPolicy.on_anthropic_stream_complete` now chains each policy's emissions through remaining policies' `on_anthropic_stream_event`, so downstream transforms (e.g. AllCapsPolicy) apply to all content including welcome messages.
