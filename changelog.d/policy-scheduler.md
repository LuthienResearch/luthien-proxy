---
category: Features
pr: 543
---

**Gateway-wide policy scheduler**: Add a small scheduling primitive that lets policies register periodic background tasks via a new `BasePolicy.register_scheduled_tasks()` lifecycle hook. Tasks are owned by the gateway's asyncio loop, survive callback exceptions, and are cancelled cleanly on policy reload and gateway shutdown.
  - New package `luthien_proxy.scheduler` with `Scheduler`, `ScheduledTaskConfig`, `RunningTask`, and `TaskStatus` primitives.
  - New admin endpoint `GET /api/admin/scheduler/tasks` exposes per-task observability (last run, status, run/error counts, next run).
  - Default no-op hook on `BasePolicy` so all existing policies are unaffected; `MultiSerialPolicy` recurses into sub-policies.
