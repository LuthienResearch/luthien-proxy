"""Integration tests for Scheduler + PolicyManager lifecycle.

These tests verify the policy-load → register → reload → cancel →
re-register flow end to end, using the real PolicyManager with a mocked
database. The point is that an existing policy picks up its scheduled
tasks on load and loses them cleanly on reload.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from luthien_proxy.policies.noop_policy import NoOpPolicy
from luthien_proxy.policy_core.base_policy import BasePolicy
from luthien_proxy.policy_manager import PolicyManager
from luthien_proxy.scheduler import ScheduledTaskConfig, Scheduler
from luthien_proxy.settings import Settings


@pytest.fixture(autouse=True)
def _dogfood_mode_disabled():
    """Keep scheduler tests deterministic regardless of environment."""
    settings = Settings(dogfood_mode=False, database_url="", redis_url="", _env_file=None)  # type: ignore[call-arg]
    with patch("luthien_proxy.settings.get_settings", return_value=settings):
        yield


class _ScheduledNoOpPolicy(BasePolicy):
    """Test-only policy that registers a single periodic task."""

    def __init__(self, task_name: str = "noop-ticker") -> None:
        self._task_name = task_name
        self.tick_count = 0

    async def _tick(self) -> None:
        self.tick_count += 1

    def register_scheduled_tasks(self, scheduler: Scheduler) -> None:
        scheduler.schedule(
            ScheduledTaskConfig(
                name=self._task_name,
                interval=timedelta(seconds=60),  # long — we never want it
                callback=self._tick,  # to actually run here
            )
        )


def _make_db_mocks(*, fetchrow_return=None):
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_pool.get_pool = AsyncMock(return_value=mock_conn)
    mock_conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    mock_conn.execute = AsyncMock()
    return mock_pool, mock_conn


def _scheduled_row():
    return {
        "policy_class_ref": (
            "tests.luthien_proxy.unit_tests.scheduler.test_policy_manager_scheduler:_ScheduledNoOpPolicy"
        ),
        "config": {},
    }


class TestPolicyManagerSchedulerLifecycle:
    """The scheduler belongs to PolicyManager and follows policy lifecycle."""

    def test_manager_exposes_scheduler(self):
        mgr = PolicyManager(db_pool=MagicMock(), redis_client=MagicMock())
        assert isinstance(mgr.scheduler, Scheduler)
        assert len(mgr.scheduler) == 0

    @pytest.mark.asyncio
    async def test_noop_policy_registers_no_tasks(self):
        """Existing policies must be unaffected by the new lifecycle hook."""
        mock_pool, _ = _make_db_mocks(
            fetchrow_return={"policy_class_ref": "luthien_proxy.policies.noop_policy:NoOpPolicy", "config": {}}
        )
        mgr = PolicyManager(db_pool=mock_pool, redis_client=MagicMock(), policy_source="db")
        try:
            await mgr.initialize()
            assert isinstance(mgr.current_policy, NoOpPolicy)
            assert len(mgr.scheduler) == 0
        finally:
            await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_policy_with_tasks_registers_on_initialize(self):
        mock_pool, _ = _make_db_mocks(fetchrow_return=_scheduled_row())
        mgr = PolicyManager(db_pool=mock_pool, redis_client=MagicMock(), policy_source="db")
        try:
            await mgr.initialize()
            assert len(mgr.scheduler) == 1
            assert mgr.scheduler.has_task("noop-ticker")
        finally:
            await mgr.shutdown()
        assert len(mgr.scheduler) == 0

    @pytest.mark.asyncio
    async def test_reload_cancels_old_and_registers_new_tasks(self):
        """enable_policy should cancel the previous policy's tasks and register the new ones."""
        mock_pool, _ = _make_db_mocks(fetchrow_return=_scheduled_row())
        mgr = PolicyManager(db_pool=mock_pool, redis_client=None, policy_source="db")
        await mgr.initialize()
        try:
            first_policy = mgr.current_policy
            assert mgr.scheduler.has_task("noop-ticker")

            # Swap to a NoOp policy — the old task should be cancelled.
            result = await mgr.enable_policy(
                policy_class_ref="luthien_proxy.policies.noop_policy:NoOpPolicy",
                config={},
                enabled_by="test",
            )
            assert result.success is True
            assert isinstance(mgr.current_policy, NoOpPolicy)
            assert mgr.current_policy is not first_policy
            assert len(mgr.scheduler) == 0

            # Swap back to the scheduled policy — new task should be registered.
            result = await mgr.enable_policy(
                policy_class_ref=(
                    "tests.luthien_proxy.unit_tests.scheduler.test_policy_manager_scheduler:_ScheduledNoOpPolicy"
                ),
                config={},
                enabled_by="test",
            )
            assert result.success is True
            assert len(mgr.scheduler) == 1
            assert mgr.scheduler.has_task("noop-ticker")
        finally:
            await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_cancels_all_tasks(self):
        mock_pool, _ = _make_db_mocks(fetchrow_return=_scheduled_row())
        mgr = PolicyManager(db_pool=mock_pool, redis_client=MagicMock(), policy_source="db")
        await mgr.initialize()
        assert len(mgr.scheduler) == 1
        await mgr.shutdown()
        assert len(mgr.scheduler) == 0
        # Idempotent — calling twice is safe.
        await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_register_failure_does_not_break_load(self):
        """A policy whose register_scheduled_tasks raises must not brick the gateway."""

        class _BrokenPolicy(BasePolicy):
            def register_scheduled_tasks(self, scheduler: Scheduler) -> None:
                raise RuntimeError("I am broken")

        mgr = PolicyManager(db_pool=MagicMock(), redis_client=MagicMock())
        # Call the internal helper directly — we just need to confirm it
        # swallows the exception and logs it.
        mgr._register_scheduled_tasks(_BrokenPolicy())
        assert len(mgr.scheduler) == 0


class TestMultiSerialRecursesIntoSubPolicies:
    """MultiSerialPolicy.register_scheduled_tasks should recurse."""

    @pytest.mark.asyncio
    async def test_multi_serial_recurses(self):
        from luthien_proxy.policies.multi_serial_policy import MultiSerialPolicy

        class _A(BasePolicy):
            def register_scheduled_tasks(self, scheduler: Scheduler) -> None:
                scheduler.schedule(ScheduledTaskConfig(name="a", interval=timedelta(seconds=60), callback=_async_noop))

        class _B(BasePolicy):
            def register_scheduled_tasks(self, scheduler: Scheduler) -> None:
                scheduler.schedule(ScheduledTaskConfig(name="b", interval=timedelta(seconds=60), callback=_async_noop))

        multi = MultiSerialPolicy.from_instances([_A(), _B()])

        scheduler = Scheduler()
        try:
            multi.register_scheduled_tasks(scheduler)
            assert scheduler.has_task("a")
            assert scheduler.has_task("b")
        finally:
            await scheduler.cancel_all()


async def _async_noop() -> None:
    return None
