-- Dynamic policies: user-created or LLM-generated policy source code
CREATE TABLE IF NOT EXISTS dynamic_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    source_code TEXT NOT NULL,
    config JSONB DEFAULT '{}',
    prompt TEXT,
    is_active BOOLEAN DEFAULT FALSE,
    version INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    created_by TEXT
);

CREATE INDEX idx_dynamic_policies_is_active ON dynamic_policies (is_active);
CREATE INDEX idx_dynamic_policies_created_at ON dynamic_policies (created_at DESC);
