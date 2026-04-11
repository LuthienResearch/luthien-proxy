# Objective: Gateway-wide policy scheduler

## Goal

Add a gateway-level scheduling primitive that lets policies register periodic background tasks. Tasks run on the gateway's asyncio loop, are tracked centrally, and are cleanly cancelled on policy reload or process shutdown.

## Motivation

Several upcoming policies need to refresh shared state on a schedule rather than per-request. The immediate driver is the **supply-chain blocklist policy** (separate PR, branch `worktree-supply-chain-blocklist`), which polls OSV every few minutes for newly-published high-severity CVEs and maintains an in-memory blocklist consulted on every bash tool_use.

The natural shape is "policies register periodic tasks at load time; the gateway owns the asyncio loop and the lifecycle." Without this primitive, each policy that wants periodic work has to spawn its own `asyncio.create_task` and manage its own cancellation, which doesn't compose, isn't observable centrally, and silently leaks tasks across policy reloads.

## Acceptance check

- A policy can register one or more periodic tasks via a new method on the policy lifecycle (e.g., `register_scheduled_tasks(scheduler) -> None`). Returning nothing or not implementing the method means no tasks (default behavior, backward compatible).
- The scheduler accepts per-task config: `name`, `interval` (timedelta), `callback` (async callable), optional `jitter`, optional `run_immediately` (bool, default False).
- Tasks are cancelled cleanly on policy reload (admin API) and on gateway shutdown. No orphaned tasks across reloads.
- Failures inside a task callback are caught, logged with full context, and the task continues to run on the next interval. One bad poll does not kill the task.
- A registered task is observable: at minimum the gateway exposes per-task metadata (last-run timestamp, last-run status, run count, error count) via the existing admin API or telemetry surface.
- Unit tests confirm: registration, execution at the configured interval, cancellation on reload, exception isolation, jitter behavior.
- Integration test confirms the scheduler interacts correctly with the existing policy lifecycle (load → schedule → reload → re-schedule → unload → cancel).
- No new heavy dependencies. `apscheduler` is allowed only if hand-rolled `asyncio.create_task` + cancellation tracking proves materially worse — default is hand-rolled.

## Non-goals

- **Cron syntax.** Interval-based only ("every N seconds/minutes"). Cron is a follow-up if a future policy needs it.
- **Distributed scheduling.** The scheduler runs in-process on each gateway instance. If two gateway instances are running, both schedule the same task — that's a known property of the in-process design and is acceptable for v1.
- **Persistence of task state across restarts.** Tasks re-register on policy load and run from a clean slate. If a policy needs persistent state across restarts, it owns its own DB schema (the supply-chain blocklist policy is doing exactly this).
- **A scheduler admin UI page.** A text/JSON endpoint is sufficient for v1; UI is a follow-up.
- **Backpressure / coordination between tasks.** Each task runs on its own interval, independent of other tasks. If two tasks need to coordinate, they share state via the policy instance.

## External contracts

- The new `register_scheduled_tasks` method on the policy lifecycle. Once landed, every existing policy gains the (no-op) default behavior; new policies can opt in.
- The admin API endpoint(s) for task observability. Whatever shape they take, they must follow the existing admin API conventions (auth, JSON shape, etc.).
- The existing policy reload path. The scheduler must hook into reload cleanly without breaking any existing policy that does not use scheduled tasks.

## Assumptions (falsifiable)

- I assume the gateway's asyncio event loop is the right place for these tasks (i.e., the gateway is a long-running async process, not a per-request worker pool). If gateway becomes pre-fork or a worker-pool model, the scheduler design needs to choose a single owner instance.
- I assume policy reloads happen via a discrete admin API call (not via filesystem watch or signal). The scheduler hooks into that explicit reload path.
- I assume `asyncio.create_task` + cancellation tracking is sufficient for v1 needs. If a future task needs cron-style scheduling or missed-tick semantics, we revisit.

## Dependencies / blockers

None — this PR can land independently and is the prerequisite for the supply-chain blocklist PR.

## Out-of-scope concerns to defer

- The supply-chain blocklist policy itself.
- Cron syntax.
- Distributed coordination.
- Scheduler admin UI page.
- Per-task metrics dashboards.

## Reference

- Existing policy lifecycle hooks: `src/luthien_proxy/policy_core/` and `src/luthien_proxy/policies/`.
- Existing admin API: `src/luthien_proxy/admin/`.
- Project rules: repo `CLAUDE.md` at the repo root.
