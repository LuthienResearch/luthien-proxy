import type { TelemetryPayload, TelemetryMetrics } from "./validate";

interface MetricMapping {
  jsonKey: keyof TelemetryMetrics;
  name: string;
}

const METRIC_MAPPINGS: MetricMapping[] = [
  { jsonKey: "input_tokens", name: "luthien_input_tokens_total" },
  { jsonKey: "output_tokens", name: "luthien_output_tokens_total" },
  { jsonKey: "requests_accepted", name: "luthien_requests_accepted_total" },
  { jsonKey: "requests_completed", name: "luthien_requests_completed_total" },
  { jsonKey: "streaming_requests", name: "luthien_streaming_requests_total" },
  { jsonKey: "non_streaming_requests", name: "luthien_non_streaming_requests_total" },
  { jsonKey: "sessions_with_ids", name: "luthien_active_sessions" },
];

function escapeTagValue(value: string): string {
  return value.replace(/\\/g, "\\\\").replace(/\n/g, "\\n").replace(/ /g, "\\ ").replace(/,/g, "\\,").replace(/=/g, "\\=");
}

export function toInfluxLines(payload: TelemetryPayload): string {
  const timestampNs = (BigInt(new Date(payload.timestamp).getTime()) * 1_000_000n).toString();
  const deploymentId = escapeTagValue(payload.deployment_id);
  const proxyVersion = escapeTagValue(payload.proxy_version);
  const tags = `deployment_id=${deploymentId},proxy_version=${proxyVersion}`;

  const lines = METRIC_MAPPINGS.map(({ jsonKey, name }) => {
    const value = payload.metrics[jsonKey];
    return `${name},${tags} value=${value}i ${timestampNs}`;
  });

  return lines.join("\n") + "\n";
}
