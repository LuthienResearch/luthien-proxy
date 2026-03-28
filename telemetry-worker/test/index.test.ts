import { describe, it, expect, vi } from "vitest";
import { handleRequest, type Env } from "../src/index";

function createMockKV(): KVNamespace {
  const store = new Map<string, string>();
  return {
    get: vi.fn(async (key: string) => store.get(key) ?? null),
    put: vi.fn(async (key: string, value: string) => { store.set(key, value); }),
    delete: vi.fn(),
    list: vi.fn(),
    getWithMetadata: vi.fn(),
  } as unknown as KVNamespace;
}

const ENV: Env = {
  GRAFANA_USER_ID: "123456",
  GRAFANA_API_KEY: "glc_test_key",
  GRAFANA_PUSH_URL: "https://influx-prod.grafana.net/api/v1/push/influx/write",
  DEDUP: createMockKV(),
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

  it("deduplicates identical payloads", async () => {
    const env = { ...ENV, DEDUP: createMockKV() };
    let grafanaCalls = 0;
    const mockFetch = async () => { grafanaCalls++; return new Response("", { status: 204 }); };

    const req1 = makeRequest("POST", VALID_BODY);
    const res1 = await handleRequest(req1, env, mockFetch);
    expect(res1.status).toBe(200);
    const body1 = await res1.json() as { deduplicated?: boolean };
    expect(body1.deduplicated).toBeUndefined();

    const req2 = makeRequest("POST", VALID_BODY);
    const res2 = await handleRequest(req2, env, mockFetch);
    expect(res2.status).toBe(200);
    const body2 = await res2.json() as { deduplicated?: boolean };
    expect(body2.deduplicated).toBe(true);

    expect(grafanaCalls).toBe(1);
  });

  it("returns 415 for non-JSON Content-Type", async () => {
    const req = new Request("https://telemetry.luthien.cc/v1/events", {
      method: "POST",
      body: "not json",
      headers: new Headers({ "Content-Type": "text/plain" }),
    });
    const res = await handleRequest(req, ENV, async () => new Response("", { status: 204 }));
    expect(res.status).toBe(415);
  });

  it("returns 400 for non-JSON body with correct Content-Type", async () => {
    const req = new Request("https://telemetry.luthien.cc/v1/events", {
      method: "POST",
      body: "this is not json at all",
      headers: new Headers({ "Content-Type": "application/json" }),
    });
    const res = await handleRequest(req, ENV, async () => new Response("", { status: 204 }));
    expect(res.status).toBe(400);
    const body = await res.json() as { error: string };
    expect(body.error).toContain("invalid JSON");
  });

  it("returns 404 for unknown paths", async () => {
    const req = new Request("https://telemetry.luthien.cc/unknown", { method: "POST" });
    const res = await handleRequest(req, ENV, async () => new Response("", { status: 204 }));
    expect(res.status).toBe(404);
  });
});
