---
category: Features
---

**OpenTelemetry observability spans and attributes** (#805): adds low-cardinality spans and attributes across credential validation, DB writes, request/response body sizes, and time-to-first-streamed-event, plus an opt-in Sentry init. All attributes are sizes, counts, durations, status, or booleans — no request/response content or credentials are recorded.
