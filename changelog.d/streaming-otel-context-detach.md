---
category: Fixes
---

**`_stream_with_keepalive` pumps the upstream generator from one task instead of a fresh task per item, fixing the `opentelemetry.context` "Failed to detach context" ERROR storm**
  - In production this logger produced roughly 60k ERROR lines per 48h,
    continuously, since streaming keepalives shipped — about twice per
    streaming request.
  - Root cause: `AnthropicClient.stream()` and `_AnthropicPolicyIO._stream()`
    each hold an OpenTelemetry span open across every chunk of the upstream
    response (`with tracer.start_as_current_span(...): async for event in
    ...: yield event`). `_stream_with_keepalive` drove that generator by
    wrapping every single `__anext__()` call in a brand-new
    `asyncio.ensure_future(...)` Task. `asyncio.Task` copies
    `contextvars.Context` at creation, so the span's context-attach token
    (created in the Task that fetched the first chunk) could not be
    detached from the different Task that fetched the last one —
    `contextvars.Context.reset()` raises `ValueError`, which
    `opentelemetry.context.detach()` catches and logs at ERROR rather than
    propagating. Reproduced directly against `_stream_with_keepalive` with
    a real `TracerProvider` (no Sentry involved): the same ERROR line and
    traceback as production. Sentry was a correlate, not the cause — it
    was already ruled out by tracing `sentry_sdk`'s default integrations
    (no OpenTelemetry-backed tracing is enabled by our `init_sentry()`).
  - **Fix**: `_stream_with_keepalive` now pumps `source` to completion from
    a single persistent task (`_pump_to_queue`) that feeds a one-slot
    queue, preserving the existing "never drop a slow item, only inject a
    keepalive" behavior and the "cancel-on-close" contract, while keeping
    every span's attach/detach pair inside one task's context.
