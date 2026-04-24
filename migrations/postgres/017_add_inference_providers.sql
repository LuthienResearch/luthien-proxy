-- Named inference providers. Operator-defined instances of the
-- `InferenceProvider` interface (see src/luthien_proxy/inference/). Used
-- by judge policies and the policy-testing UI to name a configured
-- provider rather than hardcoding backend + credential + model per policy.

CREATE TABLE IF NOT EXISTS inference_providers (
    name TEXT UNIQUE NOT NULL PRIMARY KEY,
    backend_type TEXT NOT NULL,
    credential_name TEXT,
    default_model TEXT NOT NULL,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_inference_providers_backend_type
    ON inference_providers(backend_type);
