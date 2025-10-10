# Technical Decisions

Why certain approaches were chosen over alternatives.

**Format**: Each entry is a subsection with a title, timestamp (YYYY-MM-DD), and content (decision + rationale).
If updating existing content significantly, note it: `## Topic (2025-10-08, updated 2025-11-15)`

---

## Configuration (2025-10-08)

**Decision**: Use separate YAML files for LiteLLM config (`litellm_config.yaml`) and Luthien policy config (`luthien_config.yaml`)

**Rationale**: Separates concerns - LiteLLM manages model routing, Luthien manages policy decisions. This allows independent evolution of each configuration.

## Policy Loading (2025-10-08)

**Decision**: Load policy class dynamically via `LUTHIEN_POLICY_CONFIG` environment variable

**Rationale**: Allows swapping policies without code changes, supports different policies for different environments.

## Platform Vision and Scope (2025-10-08)

**Decision**: Build general-purpose infrastructure for LLM policy enforcement that can support both simple and adversarially robust policies.

**Rationale**: The platform should enable developers to easily write and enforce policies on LLM usage, ranging from prosaic policies (rate limiting, content filtering, PII detection) to complex adversarially robust policies like Redwood Research's AI Control methodology.

The architecture (centralized control plane, thin proxy, pluggable policies) supports this range:
- Control plane can implement trusted monitoring/editing logic for adversarial control
- Policies can be simple or complex depending on use case
- Callback hooks allow interception and modification at multiple points
- Reference implementations of complex policies (like Redwood-style control) will be provided alongside the infrastructure

This is infrastructure-first: Redwood AI Control is an important use case the platform should support, not the defining architecture.

## Conversation Storage (2025-10-08)

**Decision**: Replace debug-log reconstruction with dedicated `conversation_calls`, `conversation_events`, and `conversation_tool_calls` tables. (THIS IS NOW OBSOLETE, SEE CONVERSATION TURN NORMALIZATION PLAN)

**Rationale**: Debug logs were flexible but expensive to replay and impossible to index. Structured tables let the control plane write each event once and power snapshots/trace queries via SQL instead of per-request log parsing. Live SSE still uses Redis, but Postgres is now the canonical history for UI renderings and analytics.

## Conversation Storage Schema v2 (2025-10-09, updated 2025-10-10, **COMPLETED**)

**Decision**: Simplify to three core tables:

1. **conversation_calls** - Lightweight parent record per API call
   - `call_id` (PK), `model_name`, `provider`, `status`, `created_at`, `completed_at`

2. **conversation_events** - Request/response turns in OpenAI format
   - `id` (PK), `call_id` (FK), `event_type` ('request' | 'response'), `sequence`, `payload` (jsonb), `created_at`
   - Request payload: `{"messages": [...], "model": "...", "temperature": ...}`
   - Response payload: `{"message": {...}, "finish_reason": "stop", "status": "success"}`

3. **policy_events** - Policy decisions and actions (replaces judge_decisions)
   - `id` (PK), `call_id` (FK), `policy_class`, `policy_config` (jsonb), `event_type`, `original_event_id` (FK to conversation_events), `modified_event_id` (FK to conversation_events), `metadata` (jsonb), `created_at`

**What's dropped**:

- `trace_id` columns (LiteLLM doesn't reliably provide them)
- `conversation_tool_calls` table (tool calls are in response message payload)
- `conversation_judge_decisions` table (replaced by policy_events)
- All streaming chunk storage (chunk_index, delta_text, raw_chunk, etc.)

**Rationale**:

- Store complete OpenAI-format request/response payloads instead of reconstructing from streaming chunks
- Accept redundancy in request messages array (full conversation context each turn) - Postgres jsonb compression handles this well
- Streaming chunk data only needed for live monitoring (use Redis pub/sub) and debugging (keep in debug_logs)
- Policy events table generalizes beyond just judge decisions to support any policy action/decision
- Row-based retention will prevent unbounded growth (implemented as separate background task)

**Implementation Status**:

- ✅ Schema migrated and applied to database
- ✅ Code updated (events.py, store.py, db.py, judge/db.py)
- ✅ Tests updated and passing (134 passing, 1 skipped)
- ✅ All dev_checks passing
- ✅ Live conversation monitor UI updated (conversation_monitor.js, snapshots.py)

---

(Add new decisions as they're made with timestamps: YYYY-MM-DD)
