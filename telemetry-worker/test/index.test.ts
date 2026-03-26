import { describe, it, expect } from "vitest";
import { handleRequest, type Env } from "../src/index";

const ENV: Env = {
  GRAFANA_USER_ID: "123456",
  GRAFANA_API_KEY: "glc_test_key",
  GRAFANA_PUSH_URL: "https://influx-prod.grafana.net/api/v1/push/influx/write",
};

function makeRequest(method: string, body?: unknown): Request {
  const init: RequestInit = { method };
  if (body) {
    init.body = JSON.stringify(body);
    init.headers = new Headers({ "Content-Type": "application/json" });
  }
  return new Request("https://telemetry.luthien.cc/v1/events", init);
}

const VALID_BODY = {
  schema_version: 1,
  deployment_id: "test-id",
  proxy_version: "0.1.0",
  python_version: "3.13.1",
  interval_seconds: 300,
  timestamp: "2026-03-25T22:00:00Z",
  metrics: {
    requests_accepted: 5,
    requests_completed: 5,
    input_tokens: 1000,
    output_tokens: 500,
    streaming_requests: 4,
    non_streaming_requests: 1,
    sessions_with_ids: 2,
  },
};

describe("handleRequest", () => {
  it("returns 200 for GET /health", async () => {
    const req = new Request("https://telemetry.luthien.cc/health", { method: "GET" });
    const res = await handleRequest(req, ENV, async () => new Response("", { status: 204 }));
    expect(res.status).toBe(200);
  });

  it("returns 405 for GET /v1/events", async () => {
    const req = makeRequest("GET");
    const res = await handleRequest(req, ENV, async () => new Response("", { status: 204 }));
    expect(res.status).toBe(405);
  });

  it("returns 200 for valid POST and forwards to Grafana", async () => {
    let capturedBody = "";
    const mockFetch = async (url: string | URL | Request, init?: RequestInit) => {
      capturedBody = init?.body as string;
      return new Response("", { status: 204 });
    };

    const req = makeRequest("POST", VALID_BODY);
    const res = await handleRequest(req, ENV, mockFetch);
    expect(res.status).toBe(200);

    expect(capturedBody).toContain("luthien_input_tokens_total");
    expect(capturedBody).toContain("value=1000i");
  });

  it("returns 200 even when Grafana push fails", async () => {
    const mockFetch = async () => new Response("error", { status: 500 });

    const req = makeRequest("POST", VALID_BODY);
    const res = await handleRequest(req, ENV, mockFetch);
    expect(res.status).toBe(200);
  });

  it("returns 400 for invalid payload", async () => {
    const req = makeRequest("POST", { bad: "data" });
    const res = await handleRequest(req, ENV, async () => new Response("", { status: 204 }));
    expect(res.status).toBe(400);
    const body = await res.json() as { error: string };
    expect(body.error).toBeDefined();
  });

  it("returns 413 for oversized payload", async () => {
    const oversized = { ...VALID_BODY, padding: "x".repeat(11_000) };
    const req = makeRequest("POST", oversized);
    const res = await handleRequest(req, ENV, async () => new Response("", { status: 204 }));
    expect(res.status).toBe(413);
  });

  it("returns 404 for unknown paths", async () => {
    const req = new Request("https://telemetry.luthien.cc/unknown", { method: "POST" });
    const res = await handleRequest(req, ENV, async () => new Response("", { status: 204 }));
    expect(res.status).toBe(404);
  });
});
