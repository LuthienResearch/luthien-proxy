# Objective: Supply Chain Guard Policy

Build a policy that prevents supply chain attacks by intercepting package install
tool calls (pip, npm, cargo, go, gem, composer) and checking packages against
known CVEs via the OSV.dev API. Block installs with high/critical severity
vulnerabilities and provide actionable remediation guidance.

## Scope

Single PR covering both directions:

- **Outgoing (response)**: Intercept Bash tool_use blocks with install commands
  before they reach the client. Block vulnerable installs by replacing the
  tool_use with a text block explaining the CVEs and remediation.

- **Incoming (request)**: Scan user messages for tool_result blocks from prior
  installs. If a vulnerable package was already installed, prepend a warning
  to the system prompt so the LLM knows to remediate.

## Dependencies

- **Depends on PR #521** (generic policy cache infrastructure) — must merge first.
  This policy will use `context.policy_cache("SupplyChainGuard")` from the start
  to persist OSV lookup results across requests and worker restarts.

## Design reference

- Full implementation plan: `/home/jai/.claude/plans/snazzy-dreaming-beacon.md`
- Prior design + implementation exists in the conversation transcript at
  `~/.claude/projects/-home-jai-projects-luthien-proxy/` (session
  snazzy-dreaming-beacon, 2026-04-09). Key files to recreate/reference:
  - `src/luthien_proxy/policies/supply_chain_guard_utils.py` — types, command
    parser for 7 ecosystems, OSV client, severity filter, message formatters
  - `src/luthien_proxy/policies/supply_chain_guard_policy.py` — policy class
    following `ToolCallJudgePolicy` streaming pattern, with both
    `on_anthropic_request` (incoming) and `on_anthropic_stream_event` /
    `on_anthropic_response` (outgoing) hooks
  - Unit tests for both files
- Follow the pattern in `src/luthien_proxy/policies/tool_call_judge_policy.py`
  for streaming buffer/evaluate/block/re-emit logic.

## Acceptance check

- [ ] PR #521 merged to main, this branch rebased on the merged commit
- [ ] Policy blocks `pip install <vulnerable-pkg>` in streaming + non-streaming
      responses with a clear CVE warning
- [ ] Policy detects vulnerable packages in incoming tool_results and injects
      a system prompt warning (doesn't block — install already happened)
- [ ] OSV lookup results cached in `policy_cache` table, survive restart
- [ ] Full unit test coverage (parsing edge cases, streaming events, request
      hook, OSV mock failures, allowlist, fail-open/closed)
- [ ] `scripts/dev_checks.sh` passes
- [ ] Changelog fragment added
- [ ] Draft PR opened and marked ready once review feedback addressed

## Out of scope

- Typosquatting detection (edit-distance vs popular package names) — future PR
- Version-aware OSV queries (current V1 queries by package name only) — future PR
- Detecting edits to dependency files (requirements.txt, package.json) — future PR
- Actually injecting tool calls for remediation (e.g. auto-running `pip audit`)
  — architecturally complex, deferred
