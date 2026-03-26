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
      return { ok: false, error: `Grafana returned ${response.status}: ${body}` };
    }

    return { ok: true };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return { ok: false, error: `Grafana push failed: ${message}` };
  }
}
