-- ABOUTME: Maps an opaque user_id to a human-readable display name.
-- ABOUTME: Populated from the history UI (click a user badge to set a name).

CREATE TABLE IF NOT EXISTS user_labels (
    user_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
