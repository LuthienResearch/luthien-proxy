-- Migration 018: Add agent column to request_logs
-- Track A bridge: captures x-luthien-agent header from opencode-luthien plugin
-- Indexing to be reviewed in Track B based on usage patterns
ALTER TABLE request_logs ADD COLUMN agent TEXT;
