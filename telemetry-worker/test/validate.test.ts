import { describe, it, expect } from "vitest";
import { validatePayload, type TelemetryPayload } from "../src/validate";

const VALID_PAYLOAD: TelemetryPayload = {
  schema_version: 1,
  deployment_id: "test-deploy-id",
  proxy_version: "0.1.0",
  python_version: "3.13.1",
  interval_seconds: 300,
  timestamp: "2026-03-25T22:00:00Z",
  metrics: {
    requests_accepted: 12,
    requests_completed: 11,
    input_tokens: 45000,
    output_tokens: 12000,
    streaming_requests: 10,
    non_streaming_requests: 1,
    sessions_with_ids: 3,
  },
};

describe("validatePayload", () => {
  it("accepts a valid payload", () => {
    const result = validatePayload(VALID_PAYLOAD);
    expect(result.ok).toBe(true);
  });

  it("rejects missing schema_version", () => {
    const { schema_version, ...rest } = VALID_PAYLOAD;
    const result = validatePayload(rest as any);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toContain("schema_version");
  });

  it("rejects wrong schema_version", () => {
    const result = validatePayload({ ...VALID_PAYLOAD, schema_version: 2 });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toContain("schema_version");
  });

  it("rejects missing metrics", () => {
    const { metrics, ...rest } = VALID_PAYLOAD;
    const result = validatePayload(rest as any);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toContain("metrics");
  });

  it("rejects missing deployment_id", () => {
    const { deployment_id, ...rest } = VALID_PAYLOAD;
    const result = validatePayload(rest as any);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toContain("deployment_id");
  });

  it("rejects missing proxy_version", () => {
    const { proxy_version, ...rest } = VALID_PAYLOAD;
    const result = validatePayload(rest as any);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toContain("proxy_version");
  });

  it("rejects invalid timestamp", () => {
    const result = validatePayload({ ...VALID_PAYLOAD, timestamp: "not-a-date" });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toContain("timestamp");
  });

  it("rejects missing timestamp", () => {
    const { timestamp, ...rest } = VALID_PAYLOAD;
    const result = validatePayload(rest as any);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toContain("timestamp");
  });

  it("rejects negative metric values", () => {
    const payload = { ...VALID_PAYLOAD, metrics: { ...VALID_PAYLOAD.metrics, input_tokens: -1 } };
    const result = validatePayload(payload);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toContain("input_tokens");
  });

  it("rejects NaN metric values", () => {
    const payload = { ...VALID_PAYLOAD, metrics: { ...VALID_PAYLOAD.metrics, output_tokens: NaN } };
    const result = validatePayload(payload);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toContain("output_tokens");
  });

  it("rejects non-integer metric values", () => {
    const payload = { ...VALID_PAYLOAD, metrics: { ...VALID_PAYLOAD.metrics, input_tokens: 3.14 } };
    const result = validatePayload(payload);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toContain("input_tokens");
  });

  it("rejects array payloads", () => {
    const result = validatePayload([1, 2, 3]);
    expect(result.ok).toBe(false);
  });

  it("accepts payload without optional fields", () => {
    const { python_version, interval_seconds, ...rest } = VALID_PAYLOAD;
    const result = validatePayload(rest);
    expect(result.ok).toBe(true);
  });

  it("accepts zero-value metrics", () => {
    const payload = {
      ...VALID_PAYLOAD,
      metrics: {
        requests_accepted: 0,
        requests_completed: 0,
        input_tokens: 0,
        output_tokens: 0,
        streaming_requests: 0,
        non_streaming_requests: 0,
        sessions_with_ids: 0,
      },
    };
    const result = validatePayload(payload);
    expect(result.ok).toBe(true);
  });
});
