# Objective: Supply chain blocklist policy

## Goal

A Luthien gateway policy that blocks bash tool_use install commands for known-compromised package versions. The blocklist is built and refreshed by an in-process background task that polls OSV every few minutes for newly-published CRITICAL CVEs. The request-time check is a simple in-memory lookup with PEP 440 / semver range matching, plus a literal-substring backstop. On a hit, the policy rewrites the bash `tool_use.input.command` field in place to a `sh -c '... LUTHIEN BLOCKED ... exit 42'` substitute — the cooperative LLM sees a failed command in the next turn's tool_result and relays the CVE information to the user via its normal error-reporting path.

## Motivation

Recent real-world incidents (litellm, axios) published compromised package versions where the CVE was public but the registry hadn't yanked yet. During that window — often minutes to hours — a cooperative LLM running `pip install litellm` or `npm install axios@1.6.8` would innocently install the poisoned build. We want to catch this at the proxy layer (org-wide enforcement, not client-config dependent) with **minutes-resolution** freshness on new CVEs, because most of the policy's value is the delta between CVE publication and registry yank.

## Background — three failed prior attempts

This is the **fourth** design pass on the supply-chain problem. PRs #522, #536, and #540 are all closed.

- **#522** (4,835 lines): adversarial shell parser. Seven /devil rounds of real bypasses. Closed.
- **#536** (2,520 lines): "best-effort advisory" via content injection (new text blocks alongside the flagged tool_use). Killed by a streaming protocol violation (non-monotonic block indices) and a missed primary use case (`npm ci` not in regex).
- **#540** (3,392 lines): command substitution + regex parsing of install commands. Three /devil rounds. Each round, the new layer the implementation added (parser → streaming → substitution-builder → wrapper-detection) shipped with its own crop of edge-case bugs at roughly the same density as the previous layer.

The meta-pattern devil identified in round 3: *"the policy makes assumptions about how a string-form bash command will be emitted, those assumptions are narrower than the real distribution of LLM output."* Three rounds, three layers, same shape of failure. The edge-case load is downstream of the design choice (regex-parsing free-form bash strings).

## The new design — why this one is different

The fundamental shift: **OSV lookups happen out-of-band on a background schedule, not at request time.** The request-time path is a tiny in-memory lookup that does not need to parse arbitrary bash. This collapses most of #540's edge-case layers because they only existed to work around imperfect command parsing.

- **Background task** (every 5 minutes, configurable, with jitter, via the scheduler primitive in PR `worktree-policy-scheduler`): query OSV's `/v1/query` REST API with a date filter for advisories published since `last_seen_at` with severity ≥ CRITICAL. Parse the affected version ranges and upsert into a `supply_chain_blocklist` table.
- **Persistence**: `supply_chain_blocklist (ecosystem, canonical_name, affected_range, cve_id, severity, published_at, fetched_at)` plus a per-ecosystem `last_seen_at` tracking row. Schema is shipped as **dual migrations** (`migrations/postgres/NNN_*.sql` + `migrations/sqlite/NNN_*.sql`) and accessed exclusively through `PoolProtocol` in `src/luthien_proxy/utils/db.py`. No backend-specific code.
- **In-memory state**: blocklist is loaded into a Python dict `(ecosystem, canonical_name) -> list[BlockedRange]` at policy startup with a single SELECT, and incrementally updated by the background task. Process restart re-loads from DB without re-fetching from OSV.
- **Request-time check**: on bash tool_use, buffer the block, extract `name==version` / `name@version` literals via a loose regex, look each up against the in-memory blocklist with version-range matching:
  - PyPI: PEP 440 specifier matching (use `packaging.specifiers.SpecifierSet`).
  - npm: semver range matching (use a small dependency or hand-rolled).
- **Backstop substring scan**: also check if any blocklisted *exact* `name==version` literal appears anywhere in the command string. Catches cases the regex misses (line continuations, exotic spacing, embedded in wrapper commands) without needing wrapper detection or other parser layers.
- **Substitution shape**: rewrite `tool_use.input.command` in place to `sh -c '... LUTHIEN BLOCKED: <pkg> <version> matches <cve> (<severity>) ... exit 42'`. Same block, same index, no new content blocks. The streaming layer in #540 was correct — re-derive the same shape from scratch in this PR.

## Acceptance check

- `SupplyChainBlocklistPolicy` registers a periodic background task via the new scheduler (default interval 5 minutes, jitter ±60s).
- The background task fetches OSV advisories with severity ≥ CRITICAL published since `last_seen_at`, parses affected ranges, and upserts them into the `supply_chain_blocklist` table. Failures are logged and the task continues on the next interval.
- The blocklist table is created via dual migrations (postgres + sqlite) with matched numbering, validated by `tests/luthien_proxy/integration_tests/test_migration_sync.py`.
- All persistence flows through `DatabasePool` / `PoolProtocol`. No direct asyncpg or aiosqlite imports in policy code.
- At policy startup, the blocklist is loaded into in-memory state via a single SELECT.
- At request time, the policy buffers bash tool_use blocks, extracts package literals, runs PEP 440 / semver range checks against the blocklist, **and** runs a literal-substring backstop scan, and substitutes the command on hit.
- The substituted command is emitted at the **same block index** as the original tool_use. **Tests verify monotonic `content_block_start` indices and unchanged block counts** (the test class-of-bug that killed #536 must not recur).
- Subprocess tests run the generated `sh -c` substitute through real `bash -c` and assert exit 42, stderr content, and absence of side effects (no temp file writes, attacker-quote metacharacter sandbox).
- Lockfile installs (`npm ci`, `pip install -r requirements.txt`, `yarn install --frozen-lockfile`, etc.) are explicitly out of scope. The policy docstring states this and recommends OSV-Scanner in CI for lockfile coverage.
- Module docstring explicitly states the policy is best-effort, cooperative-LLM only, and not a security boundary against adversarial obfuscation.
- An end-to-end test simulates a fresh blocklist entry being added by the background task and a subsequent flagged install being substituted.

## External contracts

- **OSV REST API**: `https://api.osv.dev/v1/query` with date filter on `published`. Document the exact query shape and the response fields we depend on (`affected[].package.{ecosystem,name}`, `affected[].ranges`, `database_specific.severity`, `id`, `published`).
- **`PoolProtocol` / `ConnectionProtocol`** in `src/luthien_proxy/utils/db.py`. Queries written in asyncpg style (`$1`, `$2` placeholders); SQLite translator handles backend differences.
- **The scheduler primitive** from PR `worktree-policy-scheduler`. This PR depends on that one landing first, OR on the scheduler interface being stable enough to stub against.
- **The Anthropic streaming protocol**: `content_block_start` indices must be monotonic across the emitted stream. The substitution must preserve the original block index and count. This is the invariant whose violation killed #536.
- **PEP 440** for PyPI version specifier matching (`packaging.specifiers.SpecifierSet`).
- **semver** for npm range matching.

## Assumptions (falsifiable)

- I assume OSV's `/v1/query` API supports a "published since" date filter or an equivalent way to fetch only newly-published advisories. If it does not, the background task strategy needs to fetch a larger window and dedupe in code, which is more bandwidth but still works.
- I assume `database_specific.severity` is reliably populated for CRITICAL CVEs in OSV. If a meaningful fraction of CRITICAL CVEs ship without this label and only have CVSS vectors, the background task will need a CVSS parser at fetch time (still out-of-band — fine — but more code).
- I assume PEP 440 and semver range matching are available via well-maintained Python libraries (`packaging` for the former; the latter may need `python-semver` or hand-rolled).
- I assume the Anthropic API will not introduce new content_block_delta types that arrive at a buffered tool_use index between the start and stop events without warning. The `_handle_block_delta` flush logic in #540 handles this case; this PR re-derives that logic from scratch.
- I assume `packaging` is already a transitive dep in the project (it is, via pip/setuptools). If not, it's a tiny add.

## Non-goals

- **Adversarial parser robustness.** Cooperative LLM only. Documented in module docstring.
- **Lockfile resolution.** Out of scope; recommend OSV-Scanner in CI for projects that need lockfile coverage.
- **Wrapper detection** (`docker run`, `sudo`, etc.). Not needed because the request-time check is a literal lookup against a small blocklist. False positives on `echo pip install foo` are acceptable because the substitution message ("pkg X is a known compromised version, here's the CVE") is the right thing to surface even in an echo context.
- **CVSS v3 / v4 parsing at runtime.** The background task filters by severity at fetch time using OSV's labels.
- **Request-time fail-modes for OSV unreachable.** The background task handles this; request-time uses cached state.
- **Operator-curated explicit blocklist override.** Useful but a follow-up; track in Trello.
- **Multi-instance coordination.** Each gateway instance polls independently. Known property; acceptable for v1.

## Dependencies / blockers

- **Depends on PR `worktree-policy-scheduler`** for the scheduler primitive. This PR cannot land until that one does. Implementation can begin with a stubbed scheduler interface.

## Out-of-scope concerns to defer

- Blocklist admin UI page (Trello ticket).
- Operator-curated explicit blocklist (Trello ticket).
- Multi-instance polling coordination (acceptable as-is).
- A "soft" warning mode that surfaces but does not block (Trello ticket if requested).

## Reference (do not lift code from these — re-derive from scratch)

- **PR #540** (closed) at branch `worktree-supply-chain-gate`. The streaming buffer pattern, substitution builder, `_handle_block_delta` flush logic, and 5 `TestStreamingShape` tests are all correct in that PR and are the parts to re-derive from scratch (re-derivation is required, not lifting — Jai called for a fresh start).
- **PR #536** (closed) at branch `worktree-supply-chain-advisory`. Reference for what content injection looks like and why it's wrong.
- **PR #522** (closed) at branch `worktree-supply-chain-guard`. Reference for what an adversarial parser layer looks like and why it's wrong.
- **Memory entry**: `feedback_db_agnostic_persistence.md` — DB-agnostic via `PoolProtocol` is mandatory.
- **Memory entry**: `project_supply_chain_intervention_shape.md` — partially obsolete (the architectural answer changed from "command substitution as the whole policy" to "scheduled background task pulls a blocklist; request-time path is a tiny lookup with command substitution as the intervention"). Update this memory once the PR lands.
- **Migration discipline**: `migrations/CLAUDE.md` for the dual-migration workflow and type translation rules.
