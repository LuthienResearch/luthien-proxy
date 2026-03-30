const DEDUP_TTL_SECONDS = 86_400; // 24 hours

async function hashKey(deploymentId: string, timestamp: string): Promise<string> {
  const salt = "luthien-telemetry-dedup-v1";
  const data = new TextEncoder().encode(`${salt}:${deploymentId}:${timestamp}`);
  const hash = await crypto.subtle.digest("SHA-256", data);
  const bytes = new Uint8Array(hash);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export async function isDuplicate(
  kv: KVNamespace,
  deploymentId: string,
  timestamp: string,
): Promise<boolean> {
  const key = await hashKey(deploymentId, timestamp);
  const existing = await kv.get(key);
  return existing !== null;
}

export async function markSeen(
  kv: KVNamespace,
  deploymentId: string,
  timestamp: string,
): Promise<void> {
  const key = await hashKey(deploymentId, timestamp);
  await kv.put(key, "1", { expirationTtl: DEDUP_TTL_SECONDS });
}
