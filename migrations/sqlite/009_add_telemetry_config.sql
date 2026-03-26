-- ABOUTME: Add telemetry_config table for usage telemetry opt-out and deployment identity
-- ABOUTME: Single-row table storing telemetry enabled state and unique deployment ID

CREATE TABLE IF NOT EXISTS telemetry_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER,
    deployment_id TEXT NOT NULL DEFAULT 'pending',
    updated_at TEXT DEFAULT (datetime('now')),
    updated_by TEXT
);

INSERT OR IGNORE INTO telemetry_config (id) VALUES (1);
