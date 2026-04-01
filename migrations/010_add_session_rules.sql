-- Session rules: per-session rules extracted from CLAUDE.md (or similar sources).
-- Each row is one rule for one session.

CREATE TABLE IF NOT EXISTS session_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id TEXT NOT NULL,
    rule_name TEXT NOT NULL,
    rule_instruction TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_session_rules_session ON session_rules(session_id);
