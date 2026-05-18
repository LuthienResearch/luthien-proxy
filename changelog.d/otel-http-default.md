---
category: Features
---

**OTLP exporter defaults to HTTP/protobuf**: distributed tracing now exports
via HTTP/protobuf instead of gRPC. HTTP works behind HTTP load balancers
(ALB, nginx, Cloudflare) where gRPC fails with `StatusCode.UNAVAILABLE`.
  - New env var `OTEL_EXPORTER_OTLP_PROTOCOL` (`http/protobuf` default; `grpc` opt-in)
  - Default `OTEL_EXPORTER_OTLP_ENDPOINT` changed from `http://tempo:4317` to
    `http://tempo:4318/v1/traces`
  - `observability/tempo/tempo.yaml` and `docker-compose.yaml` now expose the
    OTLP HTTP receiver on port 4318 alongside the gRPC receiver on 4317
  - Unknown protocol values now raise `ValueError` at startup instead of
    silently falling back, so typos surface immediately
  - **Breaking** for deployments that override `OTEL_EXPORTER_OTLP_ENDPOINT`
    without setting `OTEL_EXPORTER_OTLP_PROTOCOL=grpc`. To preserve the prior
    gRPC behavior set both:

    ```bash
    OTEL_EXPORTER_OTLP_PROTOCOL=grpc
    OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4317
    ```

Originally proposed in #562 (Sami Jawhar / @sjawhar).
