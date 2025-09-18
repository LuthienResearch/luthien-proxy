# Maintainability & Legibility Plan

A minimal, staged plan to improve types, docs, tests, and complexity while keeping tooling simple.

## Goals

- Strong types with one static checker (Pyright).
- Clear, Google‑style docstrings for public surfaces.
- Fast unit tests with modest coverage gate.
- Visible complexity reports; enable gating once stable.

## Order of Work

1. [x] Phase 0 — Tooling + Scaffold
2. [x] Phase 1 — Typing Baseline (Pyright basic)
3. [x] Phase 2 — Docstrings (Google) + lint
4. [ ] Phase 3 — Tests + Coverage Gate
5. [ ] Phase 4 — Complexity + Tightening

## Tooling Choices

- Static typing: Pyright (single checker).
- Formatter & linter: Ruff only (no Black). Rules: E/F/I/D; long-line lint (E501) ignored. Line length = 120.
- Editor: VS Code uses Ruff for format + import organization (`.vscode/settings.json`).
- Pre-commit: `ruff` and `ruff-format` hooks.
- Tests: pytest, pytest-asyncio, pytest-cov.
- Complexity: Radon report (non-gating); consider Ruff C901 gating later.
- Runtime type checking: none by default; consider selective beartype at boundaries in dev/tests only.

## Phase 0 — Tooling + Scaffold

- Consolidate config in `pyproject.toml`:
  - `[tool.ruff.lint]`: `select = ["E","F","I","D"]`, `extend-ignore = ["E501"]`.
  - `[tool.ruff.lint.pydocstyle]`: `convention = "google"`.
  - `[tool.pytest.ini_options]`: `addopts = "-q -ra --cov=src/luthien_proxy --cov-report=term-missing"`, `testpaths = ["tests"]`, `asyncio_mode = "auto"`.
  - `[tool.pyright]`: `pythonVersion = "3.13"`, `typeCheckingMode = "basic"`, `include = ["src"]`, `reportMissingTypeStubs = "none"`, `useLibraryCodeForTypes = true`.
  - Per-file ignores: exclude `migrations/**` from lint; relax docstrings under `scripts/**`.
- Minimal tests to enable fast iteration:
  - `tests/test_health.py`: call `health_check()` directly (no ASGI startup).
  - `tests/test_stream_context.py`: fake async Redis for `StreamContextStore`.
  - `tests/test_policy_loading.py`: temp YAML → `_load_policy_from_config()`.
- Dev checks & CI
  - Use `scripts/dev_checks.sh` (applies formatting and autofix before gating):
    - `uv run ruff format`
    - `uv run ruff check --fix`
    - `uv run ruff check`
    - `uv run pyright`
    - `uv run -m pytest -q`
    - `uv run radon cc -s -a src` (report-only)
  - One-off formatting: `scripts/format_all.sh`.

## Phase 1 — Typing Baseline

- Scope: public APIs in `policies/`, `control_plane/utils/`, `control_plane/stream_context.py`, `proxy/__main__.py`.
- Approach:
  - Add signatures and return types to public functions/methods.
  - Introduce small `TypedDict`/`dataclass` where payloads repeat.
  - Use `Any` narrowly where third-party types are weak (e.g., Redis client).
- CI: keep Pyright at "basic" and make it gating once baseline passes.

### Progress (Phase 1)
- [x] `proxy/__main__.py` — add function return types and typed command list
- [x] `proxy/start_proxy.py` — add return types
- [x] `policies/all_caps.py` — typed request_data, kwargs return types
- [x] `policies/noop.py` — signatures confirmed; types in place
- [x] `control_plane/app.py` — return types for public endpoints/helpers; lifespan docstring
- [x] `control_plane/utils/hooks.py` — annotated and documented extractors
- [x] `policies/engine.py` — converted to PEP 585 types; added focused docstrings

### Notes & Findings
- Unnecessary complexity avoided: kept signatures simple; used `Any` where external types are dynamic (LiteLLM payloads, FastAPI app object).
- Potential redundancy: two proxy entry points exist (`proxy/__main__.py` and `proxy/start_proxy.py`). Keep both for now; consider consolidating later if duplication grows.
- Policy hook shapes vary widely across LiteLLM; keep flexible `**kwargs: Any` for generic handlers to avoid brittle signatures.

### Open Questions / TODOs
- Should we standardize a small `TypedDict` for common hook payload fragments (e.g., request_data with `litellm_call_id`)? Might help in Phase 2/3.
- Confirm if `policies/noop.py` should implement additional hooks or if current minimal surface is sufficient (YAGNI for now).
- Revisit local LLM integration when we design new policies that use it.

## Phase 2 — Docstrings (Google)

- Scope: public modules/classes/functions; non-trivial internals. Skip trivial privates.
- Content: one-line summary; Args/Returns/Raises; capture "WHY" where helpful.
- Lint: Ruff `D` rules are enabled and gating across `src/**`; `tests/**` excluded via per-file-ignores.

## Phase 3 — Tests + Coverage Gate

- Add focused unit tests for pure logic and narrow seams (no network/DB/Redis):
  - Policies: behavior on simple payloads.
  - Hook utils: pure helpers in `control_plane/utils`.
  - Stream context: already covered by fake Redis.
- Coverage gate in CI: `--cov-fail-under=65` for `src/luthien_proxy/**`.
- Gradually raise toward ~80% as suites mature.

### Current Status
- Added offline unit tests: `test_health.py`, `test_stream_context.py`, `test_policy_loading.py`.
- Sandbox prevented running UV locally here; expected to pass in dev environment.
 - Pytest configured to show deprecation/resource warnings by default via `filterwarnings`.
 - New helper script `scripts/dev_checks.sh` runs: Ruff (format/lint), Ruff `D` (non‑gating), Pyright, tests, and Radon complexity.
 - Docstrings: baseline added across `src/**`; `ruff D` is clean. `tests/**` excluded from docstring rules to avoid noisy test docstrings.

## Phase 4 — Complexity + Tightening

- Complexity:
  - Keep Radon complexity report for visibility (non-gating; do not add a Radon gate).
  - Consider enabling Ruff `C901` gating later with `max-complexity = 10–12` (exclude tests).
- Types:
  - Raise Pyright to `strict` per subpackage once baseline is stable (e.g., `policies`, `control_plane/utils`).
  - Replace narrow `Any`s with concrete types over time.

## Success Criteria

- CI green with Pyright (basic), Ruff (E/F/I), and tests running offline.
- Public surfaces documented; Ruff `D` passes once gated.
- Coverage ≥ 65% on `src/luthien_proxy/**`, trending upward.
- No functions exceed the C901 threshold, or a clear refactor path exists.

## Risks & Mitigations

- Over-documentation → Restrict docstrings to public surfaces and non-trivial internals.
- Strictness drag → Keep Pyright basic initially; tighten per subpackage later.
- Async/IO flakiness → Use fakes and direct function calls; avoid full app startup in unit tests.

## Notes on Config Consolidation

- Prefer keeping config in `pyproject.toml`:
  - Ruff, Pytest, Pyright can live there.
  - Coverage settings via pytest-cov flags (no separate coverage file).
  - Radon runs with CLI flags (no config file needed).
- If Pyright in your environment doesn’t read `pyproject.toml`, use a single `pyrightconfig.json` as the only exception.

## Changelog / Work Log
- 2025-09-17: Phase 0 completed. Added Ruff/Pyright/Pytest config to `pyproject.toml`; created minimal offline tests; fixed Ruff config (`fix` key removed from lint section). Began Phase 1 by annotating proxy entrypoints and sample policy.
- 2025-09-17: Removed `policies/gemma_suspiciousness.py` (stale/unsalvageable after recent changes). Local LLM artifacts kept for future policies; not used currently.
- 2025-09-17: Increased Ruff `line-length` to 120 in `pyproject.toml` to reduce noise from long docstrings/URLs and keep code readable without excessive wrapping. Keep `E501` enabled; rely on the new limit and `ruff format` for shaping.
- 2025-09-17: Phase 1 completed. Switched PEP 484 `Dict/List` to builtin generics, added docstrings to `policies/engine.py`, and created `scripts/dev_checks.sh` including non‑gating Ruff `D` and Radon reports.
- 2025-09-17: Phase 2 completed (baseline). Added concise module/class/function docstrings across `src/**`; fixed D202/D205/D212 issues; excluded `tests/**` from `D` rules.
- 2025-09-17: Removed top‑level `main.py` (unused); reduces surface and avoids stray docstring/lint noise.
 - 2025-09-18: Consolidated on Ruff as sole formatter/linter; removed Black from dev deps. Ignored E501; line length set to 120. Added `.vscode/settings.json` to use Ruff for format/imports on save. Updated `scripts/dev_checks.sh` to apply formatting and autofix.
 - 2025-09-18: Refactored complexity hot spots: `extract_call_id_for_hook` (A), `_load_policy_from_config` (B), `trace_by_call_id` (A). Radon average now A (2.78). Remaining C: `proxy/__main__.py: main (C12)`; notable Bs: `debug_callback._safe (B9)`, `utils/streaming.extract_delta_text (B9)`, `app.hook_generic (B7)`.

## Complexity Snapshot (2025-09-18)

- Average cyclomatic complexity: A (2.78).
- Remaining C-grade: `src/luthien_proxy/proxy/__main__.py:13 main - C (12)`.
- Bs to watch: `_safe` (B9), `extract_delta_text` (B9), `hook_generic` (B7), `lifespan` (B6), `get_debug_page` (B6), `engine.log_decision` (B6), `engine.trigger_audit` (B6).
- Maintainability Index low A’s: `control_plane/app.py` (~40), `proxy/debug_callback.py` (~37.5).

Next refactors: reduce `proxy/__main__.py: main` to B/A; consider flattening conditionals in `_safe` and `extract_delta_text`.
