import { validatePayload, MAX_PAYLOAD_BYTES } from "./validate";
import { toInfluxLines } from "./influx";
import { pushToGrafana, type GrafanaConfig } from "./grafana";
import { isDuplicate, markSeen } from "./dedup";

export interface Env {
  GRAFANA_USER_ID: string;
  GRAFANA_API_KEY: string;
  GRAFANA_PUSH_URL: string;
  DEDUP: KVNamespace;
  RATE_LIMITER: RateLimiter;
}

type FetchFn = typeof fetch;

export async function handleRequest(
  request: Request,
  env: Env,
  fetchFn: FetchFn = fetch,
): Promise<Response> {
  const url = new URL(request.url);

  if (url.pathname === "/health" && request.method === "GET") {
    return new Response("OK", { status: 200 });
  }

  if (url.pathname === "/v1/events") {
    if (request.method !== "POST") {
      return Response.json({ error: "method not allowed" }, { status: 405 });
    }
    return handleTelemetryPost(request, env, fetchFn);
  }

  return Response.json({ error: "not found" }, { status: 404 });
}

async function handleTelemetryPost(
  request: Request,
  env: Env,
  fetchFn: FetchFn,
): Promise<Response> {
  if (env.RATE_LIMITER) {
    const ip = request.headers.get("cf-connecting-ip") ?? "unknown";
    const { success } = await env.RATE_LIMITER.limit({ key: ip });
    if (!success) {
      return Response.json({ error: "rate limited" }, { status: 429 });
    }
  }

  const contentType = request.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    return Response.json({ error: "Content-Type must be application/json" }, { status: 415 });
  }

  let bodyText: string;
  try {
    bodyText = await request.text();
  } catch {
    return Response.json({ error: "failed to read body" }, { status: 400 });
  }

  if (new TextEncoder().encode(bodyText).byteLength > MAX_PAYLOAD_BYTES) {
    return Response.json(
      { error: `payload too large (max ${MAX_PAYLOAD_BYTES} bytes)` },
      { status: 413 },
    );
  }

  let data: unknown;
  try {
    data = JSON.parse(bodyText);
  } catch {
    return Response.json({ error: "invalid JSON" }, { status: 400 });
  }

  const validation = validatePayload(data);
  if (!validation.ok) {
    return Response.json({ error: validation.error }, { status: 400 });
  }

  const { deployment_id, timestamp } = validation.payload;

  if (env.DEDUP) {
    if (await isDuplicate(env.DEDUP, deployment_id, timestamp)) {
      return Response.json({ accepted: true, deduplicated: true });
    }
    await markSeen(env.DEDUP, deployment_id, timestamp);
  }

  const influxBody = toInfluxLines(validation.payload);

  const grafanaConfig: GrafanaConfig = {
    pushUrl: env.GRAFANA_PUSH_URL,
    userId: env.GRAFANA_USER_ID,
    apiKey: env.GRAFANA_API_KEY,
  };

  const result = await pushToGrafana(influxBody, grafanaConfig, fetchFn);
  if (!result.ok) {
    console.error(`Grafana push failed: ${result.error}`);
  }

  // Always return 200 — fire-and-forget semantics
  return Response.json({ accepted: true });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    return handleRequest(request, env);
  },
} satisfies ExportedHandler<Env>;
