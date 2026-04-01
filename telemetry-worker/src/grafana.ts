export interface GrafanaConfig {
  pushUrl: string;
  userId: string;
  apiKey: string;
}

type PushResult = { ok: true } | { ok: false; error: string };

type FetchFn = typeof fetch;

export async function pushToGrafana(
  influxBody: string,
  config: GrafanaConfig,
  fetchFn: FetchFn = fetch,
): Promise<PushResult> {
  const credentials = btoa(`${config.userId}:${config.apiKey}`);

  try {
    const response = await fetchFn(config.pushUrl, {
      method: "POST",
      headers: {
        "Authorization": `Basic ${credentials}`,
        "Content-Type": "text/plain",
      },
      body: influxBody,
    });

    if (!response.ok) {
      const body = await response.text().catch(() => "");
      const truncated = body.length > 200 ? body.slice(0, 200) + "..." : body;
      return { ok: false, error: `Grafana returned ${response.status}: ${truncated}` };
    }

    return { ok: true };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return { ok: false, error: `Grafana push failed: ${message}` };
  }
}
