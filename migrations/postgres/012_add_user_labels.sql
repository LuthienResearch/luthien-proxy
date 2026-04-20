-- User labels: maps opaque user_hash to human-readable display names.
CREATE TABLE IF NOT EXISTS user_labels (
    user_hash TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
