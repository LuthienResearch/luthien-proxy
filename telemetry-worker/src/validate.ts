export interface TelemetryMetrics {
  requests_accepted: number;
  requests_completed: number;
  input_tokens: number;
  output_tokens: number;
  streaming_requests: number;
  non_streaming_requests: number;
  sessions_with_ids: number;
}

export interface TelemetryPayload {
  schema_version: number;
  deployment_id: string;
  proxy_version: string;
  python_version?: string;
  interval_seconds?: number;
  timestamp: string;
  metrics: TelemetryMetrics;
}

export const MAX_PAYLOAD_BYTES = 10_240; // 10KB

type ValidationResult =
  | { ok: true; payload: TelemetryPayload }
  | { ok: false; error: string };

const REQUIRED_METRIC_KEYS: (keyof TelemetryMetrics)[] = [
  "requests_accepted",
  "requests_completed",
  "input_tokens",
  "output_tokens",
  "streaming_requests",
  "non_streaming_requests",
  "sessions_with_ids",
];

export function validatePayload(data: unknown): ValidationResult {
  if (typeof data !== "object" || data === null || Array.isArray(data)) {
    return { ok: false, error: "payload must be a JSON object" };
  }

  const obj = data as Record<string, unknown>;

  if (obj.schema_version !== 1) {
    return { ok: false, error: "schema_version must be 1" };
  }

  if (typeof obj.deployment_id !== "string" || obj.deployment_id === "" || obj.deployment_id.length > 256) {
    return { ok: false, error: "deployment_id must be a non-empty string (max 256 chars)" };
  }

  if (typeof obj.proxy_version !== "string" || obj.proxy_version === "" || obj.proxy_version.length > 128) {
    return { ok: false, error: "proxy_version must be a non-empty string (max 128 chars)" };
  }

  if (typeof obj.timestamp !== "string" || isNaN(new Date(obj.timestamp).getTime())) {
    return { ok: false, error: "timestamp must be a valid ISO date string" };
  }

  if (typeof obj.metrics !== "object" || obj.metrics === null) {
    return { ok: false, error: "metrics must be an object" };
  }

  const metrics = obj.metrics as Record<string, unknown>;
  for (const key of REQUIRED_METRIC_KEYS) {
    if (typeof metrics[key] !== "number" || !(metrics[key] >= 0) || !Number.isInteger(metrics[key])) {
      return { ok: false, error: `metrics.${key} must be a non-negative integer` };
    }
  }

  return { ok: true, payload: data as TelemetryPayload };
}
