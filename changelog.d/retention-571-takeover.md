---
category: Features
---

**Conversation data retention with optional S3 archival**: Configurable
purge of `conversation_calls` older than `CONVERSATION_RETENTION_DAYS`,
with optional pre-purge archival to S3 (JSONL). Closes #561.
  - `CONVERSATION_RETENTION_DAYS` — purge horizon (disabled by default)
  - `ARCHIVE_S3_BUCKET` / `ARCHIVE_S3_PREFIX` — optional S3 archive target
  - `RETENTION_S3_ENCRYPTION` (`AES256` / `aws:kms` / `bucket-default`)
    and `RETENTION_S3_KMS_KEY_ID` — server-side encryption (validated at
    startup; `aws:kms` requires a customer-managed key id)
  - `RETENTION_ARCHIVE_BATCH_SIZE` — cursor-paginated batch size
  - Background `ConversationPurger` runs once at startup then every 24 h
  - Existing `idx_conversation_calls_created` (from migration 003) already
    serves the purge predicate — no new index introduced
  - **Operator note: archives contain user PII.** Each JSONL line is the
    full conversation record (request/response payloads in
    `conversation_events.payload`, judge prompts and verdicts in
    `conversation_judge_decisions`, policy decisions in `policy_events`).
    Treat the destination bucket as data-at-rest containing user content
    when classifying for compliance, IAM, and replication policies.
  - **Scope: this purger covers `conversation_calls` and the tables that
    cascade off it.** It does **not** cover `request_logs` (the
    HTTP-level table populated when `ENABLE_REQUEST_LOGGING=true`),
    which has no FK to `conversation_calls` and is on a separate
    retention model. If you enable both retention and request logging,
    plan a parallel cleanup for `request_logs` (tracked as a follow-up).
