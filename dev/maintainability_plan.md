# Maintainability & Legibility Plan

A minimal, staged plan to improve types, docs, tests, and complexity while keeping tooling simple.

## Goals

- Strong types with one static checker (Pyright).
- Clear, Google‑style docstrings for public surfaces.
- Fast unit tests with modest coverage gate.
- Visible complexity reports; enable gating once stable.

## Order of Work

1. Phase 0 — Tooling + Scaffold
2. Phase 1 — Typing Baseline (Pyright basic)
3. Phase 2 — Docstrings (Google) + lint
4. Phase 3 — Tests + Coverage Gate
5. Phase 4 — Complexity + Tightening

## Tooling Choices

- Static typing: Pyright (single checker).
- Lint/format: Ruff (format + lint). Start with E/F/I; later add D, C90.
- Tests: pytest, pytest-asyncio, pytest-cov.
- Complexity: Ruff C901 for gating; Radon report in CI (non-gating initially).
- Runtime type checking: none by default; consider selective beartype at boundaries in dev/tests only.

## Phase 0 — Tooling + Scaffold

- Consolidate config in `pyproject.toml`:
  - `[tool.ruff.lint]`: `select = ["E","F","I"]`, later add `"D","C90"`.
  - `[tool.ruff.lint.pydocstyle]`: `convention = "google"` (enable in Phase 2).
  - `[tool.pytest.ini_options]`: `addopts = "-q -ra --cov=src/luthien_control --cov-report=term-missing"`, `testpaths = ["tests"]`, `asyncio_mode = "auto"`.
  - `[tool.pyright]`: `pythonVersion = "3.13"`, `typeCheckingMode = "basic"`, `include = ["src"]`, `reportMissingTypeStubs = "none"`, `useLibraryCodeForTypes = true`.
  - Per-file ignores: exclude `migrations/**` from lint; relax docstrings under `scripts/**`.
- Minimal tests to enable fast iteration:
  - `tests/test_health.py`: call `health_check()` directly (no ASGI startup).
  - `tests/test_stream_context.py`: fake async Redis for `StreamContextStore`.
  - `tests/test_policy_loading.py`: temp YAML → `_load_policy_from_config()`.
- CI (non-gating for docstrings/complexity initially):
  - `uv run ruff format --check`
  - `uv run ruff check`
  - `uv run pyright`
  - `uv run pytest -q`
  - `uv run radon cc -s -a src` (report-only)

## Phase 1 — Typing Baseline

- Scope: public APIs in `policies/`, `control_plane/utils/`, `control_plane/stream_context.py`, `proxy/__main__.py`.
- Approach:
  - Add signatures and return types to public functions/methods.
  - Introduce small `TypedDict`/`dataclass` where payloads repeat.
  - Use `Any` narrowly where third-party types are weak (e.g., Redis client).
- CI: keep Pyright at "basic" and make it gating once baseline passes.

## Phase 2 — Docstrings (Google)

- Scope: public modules/classes/functions; non-trivial internals. Skip trivial privates.
- Content: one-line summary; Args/Returns/Raises; capture "WHY" where helpful.
- Lint: enable Ruff `D` rules; start non-gating, then gate after cleanup.

## Phase 3 — Tests + Coverage Gate

- Add focused unit tests for pure logic and narrow seams (no network/DB/Redis):
  - Policies: behavior on simple payloads.
  - Hook utils: pure helpers in `control_plane/utils`.
  - Stream context: already covered by fake Redis.
- Coverage gate in CI: `--cov-fail-under=65` for `src/luthien_control/**`.
- Gradually raise toward ~80% as suites mature.

## Phase 4 — Complexity + Tightening

- Complexity:
  - Enable Ruff `C901` gating with `max-complexity = 10–12` (exclude tests).
  - Keep Radon complexity report for visibility; only gate on Ruff.
- Types:
  - Raise Pyright to `strict` per subpackage once baseline is stable (e.g., `policies`, `control_plane/utils`).
  - Replace narrow `Any`s with concrete types over time.

## Success Criteria

- CI green with Pyright (basic), Ruff (E/F/I), and tests running offline.
- Public surfaces documented; Ruff `D` passes once gated.
- Coverage ≥ 65% on `src/luthien_control/**`, trending upward.
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
