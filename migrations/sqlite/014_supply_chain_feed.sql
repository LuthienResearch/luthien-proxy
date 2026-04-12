-- Supply chain feed: known-compromised package versions from OSV.
-- Used by SupplyChainFeedPolicy for request-time blocklist lookups.
CREATE TABLE IF NOT EXISTS supply_chain_feed (
    ecosystem    TEXT NOT NULL,
    name         TEXT NOT NULL,
    version      TEXT NOT NULL,
    cve_id       TEXT NOT NULL,
    severity     TEXT NOT NULL,
    published_at TEXT,
    modified_at  TEXT,
    fetched_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (ecosystem, name, version, cve_id)
);

CREATE INDEX IF NOT EXISTS idx_supply_chain_feed_lookup
    ON supply_chain_feed(ecosystem, name, version);

-- Cursor for incremental polling per ecosystem.
CREATE TABLE IF NOT EXISTS supply_chain_feed_cursor (
    ecosystem          TEXT PRIMARY KEY,
    last_seen_modified TEXT,
    last_refreshed_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
