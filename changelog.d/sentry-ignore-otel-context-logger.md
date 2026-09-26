---
category: Fixes
pr: 807
---

Stop Sentry capturing OpenTelemetry's own log noise: broadened the
`opentelemetry.context` / `opentelemetry.sdk.trace.export` ignores to the
whole `opentelemetry.*` logger namespace. OTel already handles its own
context-detach and exporter/collector failures and logs them itself; via the
logging integration these fired on ~every streaming request in one case
(~86k events in 13h) and, separately, on every collector-side export
rejection, and together exhausted the Sentry error quota. Telemetry-pipeline
health is the collector's own monitors' job, not Sentry's.
