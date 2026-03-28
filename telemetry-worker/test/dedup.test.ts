import { describe, it, expect, vi } from "vitest";
import { isDuplicate, markSeen } from "../src/dedup";

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

describe("dedup", () => {
  it("returns false for unseen payload", async () => {
    const kv = createMockKV();
    const result = await isDuplicate(kv, "deploy-1", "2026-03-25T22:00:00Z");
    expect(result).toBe(false);
  });

  it("returns true after markSeen", async () => {
    const kv = createMockKV();
    await markSeen(kv, "deploy-1", "2026-03-25T22:00:00Z");
    const result = await isDuplicate(kv, "deploy-1", "2026-03-25T22:00:00Z");
    expect(result).toBe(true);
  });

  it("different deployment_id is not duplicate", async () => {
    const kv = createMockKV();
    await markSeen(kv, "deploy-1", "2026-03-25T22:00:00Z");
    const result = await isDuplicate(kv, "deploy-2", "2026-03-25T22:00:00Z");
    expect(result).toBe(false);
  });

  it("different timestamp is not duplicate", async () => {
    const kv = createMockKV();
    await markSeen(kv, "deploy-1", "2026-03-25T22:00:00Z");
    const result = await isDuplicate(kv, "deploy-1", "2026-03-25T22:05:00Z");
    expect(result).toBe(false);
  });

  it("stores hashed keys, not raw identifiers", async () => {
    const kv = createMockKV();
    await markSeen(kv, "deploy-1", "2026-03-25T22:00:00Z");
    const putCall = (kv.put as ReturnType<typeof vi.fn>).mock.calls[0];
    const storedKey = putCall[0] as string;
    // Key should be a hex SHA-256 hash (64 chars), not contain the raw deployment_id
    expect(storedKey).toMatch(/^[0-9a-f]{64}$/);
    expect(storedKey).not.toContain("deploy-1");
  });

  it("sets 24h TTL on KV entries", async () => {
    const kv = createMockKV();
    await markSeen(kv, "deploy-1", "2026-03-25T22:00:00Z");
    const putCall = (kv.put as ReturnType<typeof vi.fn>).mock.calls[0];
    const options = putCall[2] as { expirationTtl: number };
    expect(options.expirationTtl).toBe(86_400);
  });
});
