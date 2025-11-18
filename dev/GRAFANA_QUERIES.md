# Grafana Query Guide for Luthien Gateway

## Overview

Logs are automatically extracted and labeled by Promtail, making them easy to query in Grafana without needing to use `| json` filters.

## Available Labels

These labels are automatically extracted and available for autocomplete in Grafana:

- `app` - Application name (always `luthien-gateway` for gateway logs)
- `detected_level` - Log level (auto-detected by promtail) (`INFO`, `WARNING`, `ERROR`, etc.)
- `logger` - Python logger name (e.g., `luthien_proxy.observability.transaction`)
- `trace_id` - OpenTelemetry trace ID for correlation
- **`record_type`** - Record type (e.g., `pipeline`) - **Use this to query by LuthienRecord type!**
- **`payload_type`** - Payload identifier for pipeline records (e.g., `client_request`, `backend_response`)
- `stage` - (Legacy) Pipeline stage, being replaced by `payload_type`
- `container_name` - Docker container name

**Note:** `transaction_id` is NOT a label (too high cardinality). Use line filters to query by transaction: `| json | transaction_id="abc-123"`

## Common Queries

### Basic Filtering

**All gateway logs:**
```logql
{app="luthien-gateway"}
```

**Filter by log level:**
```logql
{app="luthien-gateway", detected_level="WARNING"}
{app="luthien-gateway", detected_level="ERROR"}
{app="luthien-gateway", detected_level="INFO"}
```

**Filter by logger:**
```logql
{app="luthien-gateway", logger="luthien_proxy.observability.transaction"}
{app="luthien-gateway", logger="luthien_proxy.gateway_routes"}
{app="luthien-gateway", logger="opentelemetry.attributes"}
```

### Tracing and Correlation

**Follow a specific trace:**
```logql
{app="luthien-gateway", trace_id="e6e35cf6ea70b9e6429ad656e2653b56"}
```

**All logs with trace context (exclude health checks):**
```logql
{app="luthien-gateway", trace_id!="00000000000000000000000000000000"}
```

### Observability Records

**All PipelineRecord events:**
```logql
{app="luthien-gateway", record_type="pipeline"}
```

**Pipeline records by payload type:**
```logql
{app="luthien-gateway", record_type="pipeline", payload_type="client_request"}
{app="luthien-gateway", record_type="pipeline", payload_type="backend_request"}
{app="luthien-gateway", record_type="pipeline", payload_type="client_response"}
```

**Follow a specific transaction (use line filter, not label):**
```logql
{app="luthien-gateway"} | json | transaction_id="4ded3b2d-6302-4c79-9a5a-82a0a6788249"
```

**All observability transaction logs (FORMAT_TRACKING):**
```logql
{app="luthien-gateway", logger="luthien_proxy.observability.transaction"}
```

### Advanced Queries

**Combine multiple filters:**
```logql
{app="luthien-gateway", detected_level="WARNING", logger="opentelemetry.attributes"}
```

**Exclude certain loggers:**
```logql
{app="luthien-gateway", logger!~"opentelemetry.*"}
```

**Count logs by level:**
```logql
count_over_time({app="luthien-gateway"}[5m]) by (level)
```

**Rate of errors:**
```logql
rate({app="luthien-gateway", detected_level="ERROR"}[5m])
```

## Benefits Over Manual JSON Parsing

### Before (manual parsing required):
```logql
{service_name="luthien-gateway"} | json | logger="luthien_proxy.observability.transaction"
```

### After (automatic label extraction):
```logql
{app="luthien-gateway", logger="luthien_proxy.observability.transaction"}
```

**Advantages:**
- ✅ Autocomplete works for all labels
- ✅ Faster queries (labels are indexed)
- ✅ Cleaner, more readable queries
- ✅ No need to remember JSON field names
- ✅ Better performance (no runtime parsing)

## Tips

1. **Use the Label Browser**: In Grafana Explore, click "Label browser" to see all available labels and their values
2. **Start broad, then filter**: Begin with `{app="luthien-gateway"}` and add filters as needed
3. **Use regex for pattern matching**: `logger=~"luthien.*"` matches all Luthien loggers
4. **Combine with line filters**: Use `|=` for text search, `!=` for exclusion
5. **Check trace correlation**: Use the same `trace_id` across different services to follow requests
