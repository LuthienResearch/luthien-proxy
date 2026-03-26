import { describe, it, expect } from "vitest";
import { toInfluxLines } from "../src/influx";
import type { TelemetryPayload } from "../src/validate";

const PAYLOAD: TelemetryPayload = {
  schema_version: 1,
  deployment_id: "abc-123",
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

describe("toInfluxLines", () => {
  it("produces one line per metric", () => {
    const lines = toInfluxLines(PAYLOAD);
    expect(lines.split("\n").filter((l) => l.length > 0)).toHaveLength(7);
  });

  it("includes deployment_id and proxy_version labels", () => {
    const lines = toInfluxLines(PAYLOAD);
    expect(lines).toContain("deployment_id=abc-123");
    expect(lines).toContain("proxy_version=0.1.0");
  });

  it("uses correct metric names", () => {
    const lines = toInfluxLines(PAYLOAD);
    expect(lines).toContain("luthien_input_tokens_total");
    expect(lines).toContain("luthien_output_tokens_total");
    expect(lines).toContain("luthien_requests_accepted_total");
    expect(lines).toContain("luthien_requests_completed_total");
    expect(lines).toContain("luthien_streaming_requests_total");
    expect(lines).toContain("luthien_non_streaming_requests_total");
    expect(lines).toContain("luthien_active_sessions");
  });

  it("includes correct values", () => {
    const lines = toInfluxLines(PAYLOAD);
    expect(lines).toContain("luthien_input_tokens_total,deployment_id=abc-123,proxy_version=0.1.0 value=45000i");
    expect(lines).toContain("luthien_active_sessions,deployment_id=abc-123,proxy_version=0.1.0 value=3i");
  });

  it("includes timestamp in nanoseconds", () => {
    const lines = toInfluxLines(PAYLOAD);
    const expectedNs = new Date("2026-03-25T22:00:00Z").getTime() * 1_000_000;
    for (const line of lines.split("\n").filter((l) => l.length > 0)) {
      expect(line).toContain(expectedNs.toString());
    }
  });

  it("escapes special characters in label values", () => {
    const payload = { ...PAYLOAD, deployment_id: "id with spaces,commas=equals" };
    const lines = toInfluxLines(payload);
    expect(lines).toContain("deployment_id=id\\ with\\ spaces\\,commas\\=equals");
  });
});
