"""Gateway-wide scheduling primitive for policy background tasks.

Policies that need periodic work (e.g. refreshing shared state) register
tasks with the :class:`Scheduler` via the
:meth:`BasePolicy.register_scheduled_tasks` lifecycle hook. The gateway
owns the asyncio loop and the task lifecycle: tasks are cancelled on
policy reload and gateway shutdown so nothing leaks across hot-swaps.

The scheduler is deliberately small. It does not implement cron, missed
tick semantics, distributed coordination, or cross-restart state. If a
future policy needs any of those, revisit the design — but the default
expectation is "a policy asks for a coroutine to run every N seconds,
and the gateway makes it happen reliably."
"""

from luthien_proxy.scheduler.service import Scheduler
from luthien_proxy.scheduler.types import (
    RunningTask,
    ScheduledTaskConfig,
    TaskStatus,
)

__all__ = [
    "Scheduler",
    "ScheduledTaskConfig",
    "RunningTask",
    "TaskStatus",
]
