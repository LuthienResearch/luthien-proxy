import { validatePayload, MAX_PAYLOAD_BYTES } from "./validate";
import { toInfluxLines } from "./influx";
import { pushToGrafana, type GrafanaConfig } from "./grafana";

export interface Env {
  GRAFANA_USER_ID: string;
  GRAFANA_API_KEY: string;
  GRAFANA_PUSH_URL: string;
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
  // Size check — read body as text first to enforce limit
  let bodyText: string;
  try {
    bodyText = await request.text();
  } catch {
    return Response.json({ error: "failed to read body" }, { status: 400 });
  }

  if (bodyText.length > MAX_PAYLOAD_BYTES) {
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

  // Always return 200 to proxy — it ignores errors anyway
  return Response.json({ accepted: true });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    return handleRequest(request, env);
  },
} satisfies ExportedHandler<Env>;
