import { describe, it, expect, vi } from "vitest";
import { pushToGrafana, type GrafanaConfig } from "../src/grafana";

const CONFIG: GrafanaConfig = {
  pushUrl: "https://influx-prod.grafana.net/api/v1/push/influx/write",
  userId: "123456",
  apiKey: "glc_test_key",
};

describe("pushToGrafana", () => {
  it("sends POST with correct auth header", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(new Response("", { status: 204 }));

    const result = await pushToGrafana("metric value=1i 123\n", CONFIG, fetchSpy);

    expect(result.ok).toBe(true);
    expect(fetchSpy).toHaveBeenCalledOnce();

    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe(CONFIG.pushUrl);
    expect(init.method).toBe("POST");
    expect(init.body).toBe("metric value=1i 123\n");

    const authHeader = init.headers["Authorization"];
    const expected = "Basic " + btoa(`${CONFIG.userId}:${CONFIG.apiKey}`);
    expect(authHeader).toBe(expected);
  });

  it("returns error on non-2xx response", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response("bad request", { status: 400 })
    );

    const result = await pushToGrafana("bad data\n", CONFIG, fetchSpy);

    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toContain("400");
  });

  it("returns error on fetch failure", async () => {
    const fetchSpy = vi.fn().mockRejectedValue(new Error("network down"));

    const result = await pushToGrafana("data\n", CONFIG, fetchSpy);

    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toContain("network down");
  });
});
