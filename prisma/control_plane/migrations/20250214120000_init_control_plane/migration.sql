CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS "debug_logs" (
    "id" UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    "time_created" TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    "debug_type_identifier" TEXT NOT NULL,
    "jsonblob" JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS "idx_debug_logs_created_at" ON "debug_logs" ("time_created" DESC);
CREATE INDEX IF NOT EXISTS "idx_debug_logs_type" ON "debug_logs" ("debug_type_identifier");
