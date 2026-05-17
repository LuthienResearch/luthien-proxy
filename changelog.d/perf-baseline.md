---
category: Chores & Docs
pr: 753
---

**Admin UI performance baseline**: Establishes perf infrastructure and captures SQLite baseline for history/conversation pages.
  - Perf test scaffolding: isolated DB (`~/.luthien/perf.db`), seeding fixtures (sami-like, tier-100/1000/10000), and Playwright harness
  - `scripts/perf_explain.py` — captures EXPLAIN QUERY PLAN for top slow queries
  - `scripts/perf_report.py` — generates Markdown baseline report from seeded DB + query plans
  - `scripts/run_perf.sh` — orchestrates seed + test + SLO assertion workflow
  - Middleware timing (`Server-Timing` header) and payload-size contract tests
  - Query plan evidence: 2× TEMP B-TREE on `session_list`, full SCAN on `recent_calls`
  - Postgres baseline skipped (not available locally); run `./scripts/run_perf.sh --backend postgres` to capture
