---
category: Features
---

**OpenTelemetry observability spans and attributes** (#805): adds spans and attributes across credential validation, DB writes, request/response body sizes, and time-to-first-streamed-event. The proxy's own attributes are sizes, counts, durations, status, or booleans; they record no request/response content or credentials.
  - asyncpg queries are traced as spans when `OTEL_ENABLED` is on. The instrumentor's spans carry the SQL statement text, the database name and user, and the server host and port; query parameter values are not recorded.
  - A failed request-log write records its exception on the `request_log.write` span. The exception message includes the request's transaction id and the underlying error text: the database driver's, or json's for a body that cannot be serialized.
  - New `OBSERVABILITY_STDOUT_ENABLED` setting (default `true`, unchanged behaviour) turns off the emitter's full-payload stdout dump; events still reach the database and the event publisher.
