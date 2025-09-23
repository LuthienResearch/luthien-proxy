# Demo Plan: Database Drop Prevention with Luthien

## Phase 1 – Requirements & Planning

- Lock demo narrative, success metrics, and target audience impact.
- Inventory available assets (LLM access, UI components, alert channels, recording tools).
- Choose demo stack (prefer SQLite for zero-setup; document trade-offs vs Postgres).
- Assign owners for agent, policy, visuals, and presentation prep.

## Phase 2 – Harmful Baseline

- ✅ Seed a demo SQLite database with verifiable rows and an integrity check script.
  - `dev/demo_assets/demo_db.py` seeds the `accounts`/`orders` tables and stores a dataset hash plus version metadata.
  - `uv run python dev/demo_assets/demo_db.py check` confirms row counts and hashes before we run the harmful baseline.
- ✅ Build the dummy backend "LLM provider" that issues deterministic harmful SQL responses so we can repeat the drop scenario.
  - `dev/demo_assets/dummy_llm_provider.py` exposes `/v1/chat/completions` (OpenAI compatible) and always returns the destructive `DROP TABLE orders;` payload with narration for the demo.
- ✅ Select a framework that will execute the bad call.
  - We'll drive the baseline from **Claude Code** so the walkthrough mirrors the agent workflows we want to protect; it's already part of our tooling and makes screen capture easy.

## Phase 3 – Protection Policy

- Observe how tool call responses in this framework look
- build support for parsing tool call responses into the luthien control plane
- Capture baseline evidence (logs, DB diff, screen recording)
- Block the request, return an operator-friendly explanation, and persist structured decision context (call_id, rule hit, original text).

## Phase 4 – Observability & Alerts

- Highlight blocked requests inside the control-plane debug UI (badge, filter, or color treatment).
- Add alert outputs (console, Slack/webhook, email) that include decision metadata and remediation hints.
- Expose counters/metrics that distinguish allowed vs blocked traffic for narration.
- Ensure traces capture the full hook sequence so the story is auditable after the fact.

## Phase 5 – Demo Package & Rehearsal

- Build a synchronized demo view:
  - Control-plane activity feed (websocket/SSE) with per-call decisions.
  - Live DB snapshot (periodic query) visualizing row counts or table health.
  - Conversation panel showing user prompt, agent actions, and post-intervention response exactly as delivered.
- Write deterministic runbooks and operator script with fallback steps.
- Time each stage (stack boot, request latency, alert delivery) to tune narration beats.
- Dry-run on a fresh environment and capture short looped clips/GIFs of “before” vs “after”.

## Phase 6 – Polish & Scope Management

- Log deferred stretch items (e.g., lie detection) in `dev/TODO.md` with rationale.
- Confirm rehearsal schedule, hardware, and API keys.
- Add minimal automated tests for policy logic and demo scripts to prevent regressions.
- Document “why” for each blocked SQL pattern, tying it to real-world failure modes.

## Key Recommendations

1. Start by building and recording the harmful baseline. The mitigation story feels real only after we see the drop happen.
2. Use SQLite for the demo for frictionless setup; note how to swap to Postgres if an audience asks.
3. Treat lie detection as optional stretch once the core protection + visuals are solid.
4. Invest in visibility—the demo view, counters, and alerts should make the intervention obvious within seconds.
5. Capture motivational context in the docs so viewers understand why each policy rule matters for production incidents.
