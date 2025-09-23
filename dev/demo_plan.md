# Demo Plan: Database Drop Prevention with Luthien

## Phase 1 – Requirements & Planning

- Lock demo narrative, success metrics, and target audience impact.
- Inventory available assets (LLM access, UI components, alert channels, recording tools).
- Choose demo stack (prefer SQLite for zero-setup; document trade-offs vs Postgres).
- Assign owners for agent, policy, visuals, and presentation prep.

## Phase 2 – Harmful Baseline

- Seed a demo database with verifiable rows and an integrity check script.
- Build the agent workflow that connects through plain LiteLLM and reliably issues the destructive SQL.
- Capture baseline evidence (logs, DB diff, screen recording); optionally script the lying variant for stretch goals.

## Phase 3 – Protection Policy

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
