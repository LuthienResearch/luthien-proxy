# Objective: Supply chain feed policy

## Goal

A Luthien gateway policy that blocks bash `tool_use` commands which try to install a known-compromised package version. The blocklist is built and refreshed by an in-process background task that pulls OSV's **bulk-download GCS feed** every few minutes, filters to CRITICAL advisories, and stores the pre-expanded `(ecosystem, name, version) → cve_id` mapping in a DB-agnostic table. At request time, the policy does an O(1) dict lookup against the in-memory index (plus a literal-substring backstop) and, on a hit, rewrites `tool_use.input.command` in place to an `sh -c 'printf "LUTHIEN BLOCKED: ..." >&2; exit 42'` substitute. The cooperative LLM sees a failed bash command on the next turn and relays the CVE to the user via its normal error-reporting path.

## Motivation — and why we keep trying this

Recent real-world incidents (litellm, axios) published compromised package versions where the CVE was public but the registry hadn't yanked yet. During that window — often **minutes to hours** — a cooperative LLM running `pip install litellm` or `npm install axios@1.6.8` would innocently install the poisoned build. We want to catch this at the proxy layer with **minutes-resolution freshness** on new CVEs, because most of the value is the delta between CVE publication and registry yank.

This matters enough to try five times. Each prior attempt taught us something durable about the wrong shape of the solution space. See the "lessons from prior attempts" section below for the full story before implementing.

## Verified external contract (NOT assumption)

**This section is the reason the prior attempt (#544) failed and why it must exist here.** The prior OBJECTIVE.md listed "I assume OSV's `/v1/query` supports a since-filter" as a falsifiable assumption and then nobody falsified it before 1,298 lines of code shipped. The endpoint does not exist in the shape we assumed, the entire design was load-bearing on it, and the test suite bypassed the HTTP path via a fake client so the break was only discovered by /devil calling the real API.

**The contract below was verified with actual `curl` and `python3` invocations at the time of writing. The commands and responses are recorded here as evidence, and a companion test (`test_osv_feed_fixture`) will pin a captured real JSON response into the test suite so regressions are caught at unit-test time.**

### OSV bulk feed

**URL templates:**
- Per-ecosystem bulk zip: `https://storage.googleapis.com/osv-vulnerabilities/<ecosystem>/all.zip`
- Per-advisory JSON: `https://storage.googleapis.com/osv-vulnerabilities/<ecosystem>/<ID>.json`
- Listing API (GCS REST): `https://storage.googleapis.com/storage/v1/b/osv-vulnerabilities/o?prefix=<ecosystem>/&maxResults=500[&pageToken=...]`

**Ecosystems used by this policy:** `PyPI`, `npm`. (OSV also hosts `Go`, `Maven`, `crates.io`, `NuGet`, `RubyGems`, `Packagist`, `Pub`, `Hex`, `Debian`, `Alpine`, `GHC`, `Linux`, `OSS-Fuzz`, `Android`, etc. — out of scope for v1.)

**Verified bulk sizes (2026-04-11):**
- `PyPI/all.zip` = 22,075,064 bytes (~22 MB)
- `npm/all.zip` = 201,212,483 bytes (~201 MB)

**Refresh cadence:** OSV rewrites the bulk bucket every few minutes. `Cache-Control: public, max-age=3600` on the zip files; `Last-Modified` shifts by minutes across back-to-back HEAD requests. ETag + `If-Modified-Since` are supported.

**Verification commands (run these before every PR that touches the OSV client):**

```
$ curl -sI "https://storage.googleapis.com/osv-vulnerabilities/PyPI/all.zip"
HTTP/2 200
content-type: application/zip
content-length: 22075064
last-modified: Sun, 12 Apr 2026 00:27:50 GMT
cache-control: public, max-age=3600
...

$ curl -sI "https://storage.googleapis.com/osv-vulnerabilities/npm/all.zip"
HTTP/2 200
content-length: 201212483
...

$ curl -s "https://storage.googleapis.com/storage/v1/b/osv-vulnerabilities/o?prefix=PyPI/&maxResults=3"
{
  "kind": "storage#objects",
  "nextPageToken": "...",
  "items": [
    {"name": "PyPI/GHSA-227r-w5j2-6243.json", "updated": "...", "timeCreated": "...", "size": "3167", ...},
    ...
  ]
}
```

### Vuln JSON shape (verified against real `GHSA-227r-w5j2-6243.json`)

```json
{
  "schema_version": "1.7.3",
  "id": "GHSA-227r-w5j2-6243",
  "published": "2025-03-20T12:32:41Z",
  "modified": "2025-10-16T07:56:39.452480Z",
  "aliases": ["CVE-2024-11042"],
  "summary": "InvokeAI Arbitrary File Deletion vulnerability",
  "details": "...",
  "affected": [
    {
      "package": {
        "name": "invokeai",
        "ecosystem": "PyPI",
        "purl": "pkg:pypi/invokeai"
      },
      "ranges": [
        {"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "5.3.0rc1"}]}
      ],
      "versions": [
        "2.2.4.5", "2.2.4.6", "2.2.5", "2.3.0", ...
      ]
    }
  ],
  "severity": [
    {"type": "CVSS_V3", "score": "CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:H/A:H"}
  ],
  "database_specific": {
    "cwe_ids": ["CWE-20", "CWE-22", "CWE-73"],
    "github_reviewed": true,
    "github_reviewed_at": "2025-03-21T16:32:50Z",
    "nvd_published_at": "2025-03-20T10:15:23Z",
    "severity": "CRITICAL"
  }
}
```

**Fields the policy consumes:**

- `id` — CVE ID for the blocklist record and the substitution message.
- `published` — used as the `published_at` column in the DB.
- `modified` — used for the incremental-update watermark.
- `database_specific.severity` — a **pre-computed label string** (`"CRITICAL"`, `"HIGH"`, `"MODERATE"`, `"LOW"`, or missing). We filter to `"CRITICAL"` at parse time. **No CVSS vector parsing at runtime.** If the field is missing, the advisory is skipped (logged at INFO).
- `affected[].package.{ecosystem, name}` — key fields.
- `affected[].versions[]` — a **pre-expanded literal list of affected version strings**. We store these directly as `(ecosystem, name, version) → cve_id` tuples. **No PEP 440 / semver range matching at runtime.** If `versions` is empty or missing but `ranges` is present, we skip this advisory in v1 (logged at INFO) — range-only advisories are a follow-up.

**Fields the policy deliberately does NOT touch:**

- `severity[]` (CVSS vectors). See above.
- `details` (free-text, untrusted, prompt-injection surface).
- `references[]` (URLs, unvetted).
- `database_specific.cwe_ids`, `summary` — not useful for the block decision.

### Expected freshness

**Measured on 2026-04-11:** OSV bulk zips refresh every few minutes, GHSA→OSV ingestion is documented as near-real-time, and most PyPI/npm CVEs publish at ~1-5/day average rates (bursty). The combined CVE-public-to-policy-blocks pipeline, with a 5-minute poll interval on our side, should converge within ~10-35 minutes in the worst case. This is well inside the "delta between CVE publication and registry yank" window the policy exists to cover.

## The design

### Layout

- **Database table** `supply_chain_feed` (dual-migrated postgres + sqlite via `PoolProtocol`):
  - `(ecosystem TEXT, name TEXT, version TEXT, cve_id TEXT, severity TEXT, published_at TIMESTAMPTZ, modified_at TIMESTAMPTZ, fetched_at TIMESTAMPTZ)`
  - Primary key: `(ecosystem, name, version, cve_id)` (a given version can be flagged by multiple CVEs).
  - Index on `(ecosystem, name, version)` for O(1) request-time lookup during warm reload.
  - Separate `supply_chain_feed_cursor (ecosystem TEXT PRIMARY KEY, last_seen_modified TIMESTAMPTZ, last_refreshed_at TIMESTAMPTZ)` for incremental polling.

- **Background task** (registered via the `register_scheduled_tasks(scheduler)` hook from PR A `worktree-policy-scheduler`):
  - Default interval: 5 minutes, jitter ±60s.
  - On first run per ecosystem (no cursor): **cold start** — download the bulk `all.zip`, parse every entry, filter to CRITICAL-severity + present-`affected[].versions[]`, upsert into DB. Set cursor to max `modified` seen.
  - On subsequent runs: **incremental** — paginate the GCS listing API with `prefix=<ecosystem>/`, client-side filter items whose `updated` is newer than the cursor, fetch individual JSONs for those items only, parse and upsert. Advance cursor to max `modified` seen across the new batch.
  - On any failure: log at WARNING, do not advance the cursor, retry next tick. An OSV outage is handled by stale-but-valid in-memory state; request-time never queries OSV directly.

- **In-memory index**: on policy startup, load the full `supply_chain_feed` table into a single `dict[tuple[str, str, str], list[str]]` keyed by `(canonical_ecosystem, canonical_name, version) → [cve_id, ...]`. Rebuilt from scratch on process restart (single SELECT). Incrementally updated by the background task after each successful poll (atomic swap under a lock — see "request-time check" below).

- **Request-time check** on every bash `tool_use`:
  1. Buffer the tool_use's `input_json_delta` events until `content_block_stop`.
  2. Parse the accumulated `input_json` to extract the `command` string.
  3. **Loose regex extract**: find all `name==version` / `name@version` literals in the command (no wrapper detection, no requirement-file extraction, no line-continuation normalization — all out of scope, see "non-goals").
  4. For each `(name, version)` extracted, canonicalize (PEP 503 for PyPI, lowercase-ecosystem for npm) and look up in the in-memory index.
  5. **Backstop**: if no regex match fires, do a literal-substring scan — check if any blocklisted exact `<name><sep><version>` literal (e.g., `axios@1.6.8`, `litellm==1.59.0`) appears anywhere in the command string. Catches line-continuation cases, wrapper contexts, etc.
  6. If either check fires, rewrite `tool_use.input.command` in place to `sh -c 'printf "LUTHIEN BLOCKED: <pkg> <version> matches <cve> (CRITICAL). See https://osv.dev/vulnerability/<cve>" >&2; exit 42'`. Same block, same index, no new content blocks.
  7. Otherwise, emit the tool_use unchanged.

- **Substitution message** contains only strings we control: CVE ID, package name, version, severity label, and the OSV URL. **Zero untrusted OSV `summary` / `details` / `references` text reaches the LLM.** This kills the prompt-injection surface that haunted #536 and #544.

- **Lockfile installs** (`npm ci`, `pip install -r requirements.txt`, `yarn install --frozen-lockfile`, etc.) are **explicitly out of scope**. The module docstring says so and recommends OSV-Scanner in CI for lockfile coverage.

### What changes vs the #544 design

Same overall shape (background task + in-memory index + command substitution), but with the API contract actually verified:

| Concern | #544 | #545 (this) |
|---|---|---|
| OSV endpoint | `POST /v1/query` with fake payload | `GET` on GCS bucket, verified live |
| Severity filter | CVSS parsing at runtime, v4 fallback logic | Read pre-computed `database_specific.severity` label |
| Version matching | Hand-rolled PEP 440 + semver range matchers | Flat dict lookup against pre-expanded `affected[].versions[]` |
| Bootstrap | `initial_lookback_days` field (dead, never wired) | Cold-start downloads bulk zip, pinned in test fixture |
| Cursor | `supply_chain_feed_cursor.last_published` (wrong direction) | `last_seen_modified` for incremental listing |
| Test contract | `_FakeOSVClient` bypassing HTTP entirely | One captured real-JSON fixture test per code path |
| Persistence | Gap — `db_pool` never threaded through `_instantiate_policy` | Must be resolved; see "db_pool integration gap" below |

## Acceptance check

- `SupplyChainFeedPolicy` registers a periodic background task via the scheduler from PR `worktree-policy-scheduler` (default 5 min, jitter ±60s).
- The background task implements both the cold-start (bulk zip download) and incremental (listing API) paths, with fallback from incremental to cold-start on failure.
- The policy ships **one unit test per code path** that uses a **captured real OSV response** (zip fixture or JSON fixture) rather than hand-built dicts. This is mandatory — no exceptions. See "mandatory test fixtures" below.
- Cold-start test: pin a minimal real zip containing ~5 advisories in `tests/fixtures/osv/pypi_sample.zip`. Unit test reads it, parses, and asserts the expected blocklist entries materialize.
- Incremental test: pin a captured listing-API response and a few individual vuln JSONs. Unit test walks the incremental code path end-to-end.
- Real-network smoke test (marked `@pytest.mark.osv_live`, excluded from `uv run pytest` by default but run manually before marking the PR ready): hits the real OSV GCS endpoints and asserts HTTP 200 + parseable ZIP/JSON. This is the test that would have caught #544's fatal.
- Dual migrations `NNN_supply_chain_feed.sql` in `migrations/postgres/` and `migrations/sqlite/`, validated by `tests/luthien_proxy/integration_tests/test_migration_sync.py`. Copy the sqlite migration to `src/luthien_proxy/utils/sqlite_migrations/` per `migrations/CLAUDE.md` step 4.
- **All persistence flows through `PoolProtocol`**. No direct `asyncpg` or `aiosqlite` imports in policy code.
- **`db_pool` is wired through `_instantiate_policy`**. This is the gap #544 documented as a TODO and never closed. See "db_pool integration gap" below for the fix.
- Streaming-shape correctness tests: at minimum `test_flagged_tool_use_preserves_block_index`, `test_flagged_tool_use_preserves_block_count`, `test_two_flagged_tool_uses_in_one_response`, `test_monotonic_block_start_across_stream`, `test_flagged_tool_use_rewrites_command_field`. Each asserts specific `event.index` values with explicit equality, not just event existence.
- Subprocess-execution tests for the substitute builder: run the generated `sh -c` through real `bash -c` with clean, attacker-quote (`'; touch /tmp/PWN; '`), backtick, and `$(...)` variants. All assert exit 42, `LUTHIEN BLOCKED` in stderr, clean cwd.
- Substring-backstop test: blocklist contains literal `axios@1.6.8`, command is `echo "axios@1.6.8 is bad"` — backstop fires, substitute emitted. (False positives on documentation reads are an acknowledged tradeoff — see non-goals.)
- Module docstring explicitly states: best-effort, cooperative-LLM only, not a security boundary, run OSV-Scanner in CI for lockfile coverage.
- Source target: **under 1000 lines total across policy + utils + db**. If you overshoot, stop and justify. The prior attempt overshot 800 → 1298; the bloat turned out to be dead fields from an abandoned pivot.

## Mandatory test fixtures

**These are mandatory because the load-bearing assumption in the last four rounds has been "the test suite, which passes against mocks, validates the contract with the external system." It does not. The next attempt breaks the pattern by pinning captured real responses.**

1. `tests/fixtures/osv/pypi_sample.zip` — a captured bulk zip containing ~5 real advisory JSONs (mixture of CRITICAL, HIGH, and MODERATE severities). Created by downloading `https://storage.googleapis.com/osv-vulnerabilities/PyPI/all.zip` and extracting 5 representative entries into a smaller zip. The file is committed to the repo.
2. `tests/fixtures/osv/pypi_sample_listing.json` — a captured GCS listing response with ~10 items.
3. `tests/fixtures/osv/GHSA-<id>.json` — one or two individual vuln JSONs, used for unit tests of `_parse_vuln_entry`.
4. A pytest mark `osv_live` (or similar) for the real-network smoke test. Documented in `pyproject.toml` or `conftest.py`.

## `db_pool` integration gap — must be fixed in this PR

The prior attempt (#544) left a TODO saying:

> the policy is only driven by tests... and therefore runs with an empty blocklist in any production deployment

The gap: `src/luthien_proxy/policy_core/config.py:_instantiate_policy` only spreads YAML config kwargs into the policy constructor. There is no mechanism to pass a `db_pool` (or a scheduler, or a settings object) from `PolicyManager` into the policy at construction time. Policies that need shared gateway services cannot currently be wired up through the normal load path.

**This PR must close that gap.** Either:

- **Option A**: add a new lifecycle method `on_policy_loaded(self, context: PolicyLoadContext) -> None` where `PolicyLoadContext` carries the `db_pool`, the scheduler, and any other gateway services. `PolicyManager.initialize()` calls this after `_instantiate_policy`. Default implementation is a no-op.
- **Option B**: change the `_instantiate_policy` signature to accept gateway services and spread them as kwargs (`db_pool=..., scheduler=...`). Every existing policy ignores them; new policies opt in by declaring matching constructor parameters.

Pick the one that causes fewer changes across existing policies. I (the OBJECTIVE author) lean Option A because it's additive and doesn't risk breaking any existing policy's constructor signature.

**The scheduler PR** (`worktree-policy-scheduler`, PR #543) should either land first and include this mechanism, or this PR should contribute the mechanism. Whichever gets there first wins. Coordinate with the reviewer on sequencing.

## Non-goals

- **Adversarial parser robustness.** Cooperative LLM only. Documented in module docstring.
- **Lockfile resolution.** Out of scope; recommend OSV-Scanner in CI.
- **Wrapper detection** (`docker run`, `sudo`, `kubectl exec`, etc.). Not needed because the request-time check is a literal lookup against a small blocklist. False positives on `echo pip install foo` and `cat docs/incident-axios@1.6.8-postmortem.md` are acknowledged acceptable — the substitution message ("X is a known compromised version") is the right thing to surface even in those contexts. If they cause real operator friction, revisit in a follow-up.
- **Range matching at runtime.** OSV provides pre-expanded version lists in `affected[].versions[]`. Use them. If a future advisory uses ranges without an expanded list, we skip it at parse time (logged at INFO) and revisit in a follow-up.
- **CVSS parsing at runtime.** `database_specific.severity` is a pre-computed label. Use it. If it's missing, the advisory is skipped.
- **Request-time fail-modes for OSV unreachable.** The background task handles OSV outages out-of-band. Request-time reads only in-memory state.
- **Operator-curated explicit blocklist override.** Follow-up (Trello ticket).
- **Multi-instance polling coordination.** Each gateway instance polls independently. Known property, acceptable for v1.
- **Ecosystems beyond PyPI + npm.** Go, Maven, RubyGems, crates.io, etc. are follow-ups.

## Dependencies / blockers

- **PR `worktree-policy-scheduler`** (PR #543) — the scheduler primitive. Either lands first (preferred) or this PR opens a parallel `on_policy_loaded` lifecycle hook and both PRs coordinate.
- The `db_pool` integration gap (above) must be closed somewhere — either here or in the scheduler PR.

## Assumptions (kept small, explicitly labeled)

- I assume `httpx.AsyncClient` supports streaming-download of a 200MB file without buffering the full body in memory. This is the standard httpx behavior for responses used via `stream()` context manager — verify.
- I assume `zipfile.ZipFile` can parse a zipstream from a `BytesIO` buffer or from an `httpx` streamed download. Verify with the actual test fixture.
- I assume the GCS listing API's `updated` field is monotonically non-decreasing within a bucket refresh cycle, so paginating with a client-side `updated > cursor` filter is consistent. If this turns out not to be true, the incremental path regresses to a safe stale-read and the cold-start path still works.
- I assume the in-memory index fits comfortably in RAM after filtering to CRITICAL. Back-of-envelope: PyPI + npm CRITICAL vulns total a few thousand entries × average few-dozen expanded versions each = low six figures of tuples, each ~50 bytes of string data = low tens of MB.

**Verify each assumption with a real test before shipping.** The feedback rule on falsifying external APIs before dispatch (see `feedback_falsify_external_apis_before_dispatch.md`) now also covers library assumptions.

## Lessons from the four prior attempts

This is the fifth attempt at this feature. Every prior attempt was killed by /devil. Every round taught us a different class-of-bug. Read this section before implementing; the class-of-bug for round 5 will not look like the class-of-bugs for rounds 1-4, but knowing their shape helps you spot round 5's.

### PR #522 — adversarial shell parser (4,835 lines, seven /devil rounds, closed)

**Shape:** tried to parse arbitrary bash commands to extract install intent from chains, wrappers, `sh -c` recursion, `env --`, `.tar.gz` URLs, VCS installs, etc.

**Failure mode:** each /devil round found a new adversarial bypass. Seven rounds. Parser layers stacked: `_normalise_chain_operators`, `_strip_wrapper_prefix`, `_detect_unsupported_install_form`, `_find_hard_block_reason_recursive`, `_split_on_chaining`, `sh -c` recursion, etc. Each layer added ~200 lines and introduced new edge cases.

**Lesson:** adversarial parser robustness against a model that can emit arbitrary bash is a losing game. Cooperative-only is the only viable scope. **Round 5 applies this by refusing to parse bash beyond a loose regex for common install forms.**

### PR #536 — content injection (2,520 lines, one /devil round, closed)

**Shape:** pivoted to "best-effort advisory for cooperative LLMs." When a flagged install was detected, **injected a new text content block alongside the tool_use** with a human-readable warning.

**Failure modes:**
1. **FATAL streaming bug**: injected the advisory at `event.index + 1000` to place it "before" the tool_use. Violated monotonic `content_block_start` ordering. Real Anthropic clients would see a protocol error on every flagged install. 126 tests, zero of them asserted `event.index` values.
2. **FATAL missed use case**: `npm ci` (the literal command that reinstalls a compromised axios from `package-lock.json`) was not in the install regex verb group. The stated primary scenario silently failed.
3. `requirements.txt` was sent to OSV as a package name because the `-r file.txt` flag was not filtered.
4. CVSS v4 vectors were silently promoted to HIGH as a fail-safe, which with default threshold HIGH was a pure noise generator.
5. Non-streaming "injection" mutated the already-generated assistant response, depending on undefined client rendering order.
6. Decorative `<untrusted OSV advisory text>` prefix with no closing delimiter and no sanitizer.

**Lessons:**
- **Content injection is the wrong intervention shape at the proxy layer.** The Anthropic API does not have a "proxy-inserted advisory" primitive. It has tool_use blocks whose content we can rewrite. Work with the primitive you have, not one you wish existed.
- **The streaming layer must have tests that assert specific `event.index` values.** #536's tests passed green while the protocol was broken.
- **Round 5 applies this by keeping the substitution shape** (rewrite `tool_use.input.command` in place, same block, same index, no new content blocks) and by inheriting the mandatory streaming-shape test list.

### PR #540 — command substitution + regex parsing (3,392 lines, three /devil rounds, closed)

**Shape:** correct intervention shape (command substitution), but still parsed bash at request time and queried OSV at request time.

**Failure modes — round 1:**
- Lockfile dry-runs used hardcoded filenames (`pip install -r dev-requirements.txt` substituted with a dry-run against `requirements.txt` — wrong file).
- `yarn install --mode=skip-build` is Yarn Berry only AND not actually a dry-run.
- `explicit_blocklist` had no PEP 503 normalization (`PyPI:Pillow:10.0.0` blocklist + `pip install pillow==10.0.0` = no match).
- `docker run ... pip install ...` rewrote the user's docker command.
- Line continuations broke the args regex.
- `_handle_block_delta` silent fallthrough on unexpected delta types.
- No `subprocess.run` tests on substitute builders.

**Failure modes — round 2 (after a tight fix-up):**
- `sudo docker run ... pip install ...` extracted the install because `sudo` wasn't in the wrapper-list but the wrapper-list was positioned as the first token check.
- `docker-compose` (hyphenated legacy form) not in the wrapper allowlist.
- CRLF line continuations (`\\\r\n`) not handled.
- Multiple `-r file` flags: only the first captured.
- Concatenated `-rfile` form (`-rrequirements.txt`) not parsed.
- `echo pip install foo` rewrites the echo.

**Round 3 verdict:** YELLOW. Each round, the new layer the implementation added shipped with the same density of edge-case bugs as the previous layer. Devil's framing: *"the policy makes assumptions about how a string-form bash command will be emitted, those assumptions are narrower than the real distribution of LLM output."* Three rounds, three layers, same shape of failure.

**Lesson:** **regex parsing of free-form bash has an irreducible edge-case floor.** The only way to break the pattern is to not parse bash as the load-bearing check. **Round 5 applies this by making the blocklist lookup the authoritative security boundary** and treating the regex as a cheap optimization for common cases, with a literal-substring backstop for everything else. If the regex misses `pipx install` or `python -m pip install` or a line continuation, the substring backstop catches it anyway, and if the substring backstop misses it, the worst case is "we don't block this specific install" (equivalent to not having the policy at all for that one case) — not "we silently rewrite the wrong thing."

### PR #544 — background task + in-memory index (3,392 lines, one /devil round, closed)

**Shape:** moved OSV queries out of the request path entirely to an every-5-minute background task that populates an in-memory blocklist.

**Failure modes:**

1. **FATAL**: the background task was built on `POST /v1/query` with a payload like `{"package": {"ecosystem": "PyPI"}}`. **OSV's `/v1/query` endpoint does not accept that request shape.** It requires `package.{name, ecosystem}` (or `purl`, or top-level `commit`). Live response: `{"code":3,"message":"Invalid query."}`. The entire background task silently no-oped every tick with a WARNING log, and the blocklist remained empty forever in production.
2. **FATAL**: `_instantiate_policy` in `config.py` only spread YAML kwargs, so the policy's `db_pool` parameter was always `None` in production. The TODO at `policy.py:531-537` acknowledged this. **The policy was unwireable end-to-end** regardless of the OSV bug.
3. 86 tests against a `_FakeOSVClient` that bypassed the HTTP path entirely. Zero tests hit real OSV bytes.
4. Two dead config fields (`osv_fetch_url`, `initial_lookback_days`) — geological record of an abandoned cold-start design pivot that never got finished.
5. Substring backstop fires on URL fragments (`pkg:npm/axios@1.6.8`), documentation reads (`cat docs/incident-axios@1.6.8-postmortem.md`), blog snippets, etc. OBJECTIVE acknowledged `echo pip install` false positives but the surface was broader than that.

**Devil's verdict:** RED. Not because any one bug was unfixable, but because the load-bearing assumption about the external API was false, the test suite was structurally incapable of catching it, and the OBJECTIVE.md listed it as a falsifiable assumption that nobody actually falsified.

**Lessons:**

- **Falsify external API assumptions with real calls BEFORE writing any code.** Not as a "falsifiable assumption" to be checked later. Falsifiable assumptions are only useful if someone actually falsifies them.
- **Every mock-based test needs a companion fixture-based test.** A captured real response pinned into the test suite is the load-bearing test that catches contract drift.
- **Integration gaps in the policy-loader are a real class of bug.** A PR that ships "a policy that works in unit tests against an in-memory pool but cannot be instantiated with a real pool in production" is a broken PR regardless of the unit tests' green status. `db_pool` wiring must be part of round 5's acceptance.

### The meta-pattern across all four rounds

| Round | Load-bearing layer | Test-blindness shape |
|---|---|---|
| #522 | adversarial shell parser | regex-only unit tests |
| #536 | streaming block-index protocol | event types and counts (not `.index`) |
| #540 r1 | substitution-builder against real shells | wrapper-detection regex behavior |
| #540 r2 | wrapper-detection edge cases | builder-only contract |
| #544 | OSV REST API request shape | FakeOSVClient bypassing HTTP |

**Devil's generalization after round 4:** *"each new layer the implementation adds ships with the same density of edge-case bugs as the previous layer, and the test suite only checks the layers the agent thought to check."*

**The round-5 bet:** moving from "parse bash + query OSV at request time" to "pull a flat blocklist out-of-band + do O(1) dict lookups at request time" collapses the layers that kept growing edge-case bugs. The remaining load-bearing assumption is the OSV GCS contract, which is **verified in this OBJECTIVE.md with recorded commands and responses**, and which will be additionally pinned by captured-real-response test fixtures. If round 5 fails, the failure will be at a **different** layer — most likely the `db_pool` integration path or the listing-API pagination semantics. Those are the places to stress-test first.

## References

- **Verified OSV contract section above** — this is the contract the implementation must match.
- **`migrations/CLAUDE.md`** — dual-migration workflow and type translation rules.
- **`src/luthien_proxy/utils/db.py`** — `PoolProtocol` / `ConnectionProtocol` abstractions. All persistence goes through these.
- **PR #522** — branch `worktree-supply-chain-guard` (closed). Reference for the adversarial-parser shape we rejected.
- **PR #536** — branch `worktree-supply-chain-advisory` (closed). Reference for the content-injection shape we rejected.
- **PR #540** — branch `worktree-supply-chain-gate` (closed). The streaming layer is correct; the request-time OSV approach was wrong. Re-derive the streaming buffer + substitution builder from scratch; do not lift code.
- **PR #544** — branch `worktree-supply-chain-blocklist` (closed). The background-task + in-memory-index architecture is right; the OSV endpoint and the `db_pool` wiring were wrong. Re-derive the DB layer and the background task from scratch with the correct API.
- **PR #543** — branch `worktree-policy-scheduler` (open draft). Provides the scheduler primitive this policy consumes. Coordinate sequencing.
- **Memory entry `feedback_falsify_external_apis_before_dispatch.md`** — the rule this OBJECTIVE was written to honor.
- **Memory entry `feedback_db_agnostic_persistence.md`** — the DB-agnostic discipline this OBJECTIVE follows.
- **Memory entry `project_supply_chain_intervention_shape.md`** — the "command substitution, not content injection" shape decision from earlier rounds. Still load-bearing.
