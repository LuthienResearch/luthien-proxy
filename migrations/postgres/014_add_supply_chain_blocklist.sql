-- Supply-chain blocklist populated by an in-process background task that polls
-- OSV for newly-published CRITICAL CVEs. Loaded into an in-memory dict at
-- policy startup. Request-time lookups never touch this table.
CREATE TABLE IF NOT EXISTS supply_chain_blocklist (
    ecosystem      TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    cve_id         TEXT NOT NULL,
    affected_range TEXT NOT NULL,
    severity       TEXT NOT NULL,
    published_at   TIMESTAMPTZ NOT NULL,
    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ecosystem, canonical_name, cve_id, affected_range)
);

CREATE INDEX IF NOT EXISTS idx_supply_chain_blocklist_lookup
    ON supply_chain_blocklist(ecosystem, canonical_name);

-- Tracks the last `published_at` timestamp seen per ecosystem so the poller can
-- fetch only newly-published advisories on subsequent ticks.
CREATE TABLE IF NOT EXISTS supply_chain_blocklist_cursor (
    ecosystem     TEXT PRIMARY KEY,
    last_seen_at  TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
