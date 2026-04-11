"""Unit tests for GET /api/admin/scheduler/tasks."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from luthien_proxy.admin.routes import (
    ScheduledTasksResponse,
    list_scheduled_tasks,
)
from luthien_proxy.scheduler.types import TaskStatus

AUTH_TOKEN = "test-admin-key"


def _mock_manager(statuses: list[TaskStatus]):
    manager = MagicMock()
    manager.scheduler = MagicMock()
    manager.scheduler.task_status = MagicMock(return_value=statuses)
    return manager


class TestListScheduledTasksRoute:
    @pytest.mark.asyncio
    async def test_empty_scheduler_returns_empty_list(self):
        manager = _mock_manager([])
        result = await list_scheduled_tasks(_=AUTH_TOKEN, manager=manager)
        assert isinstance(result, ScheduledTasksResponse)
        assert result.count == 0
        assert result.tasks == []

    @pytest.mark.asyncio
    async def test_tasks_are_serialized_from_task_status_snapshots(self):
        status = TaskStatus(
            name="osv-poll",
            interval_seconds=300.0,
            jitter_seconds=10.0,
            run_count=7,
            error_count=1,
            last_run_at="2026-04-10T12:00:00+00:00",
            last_run_status="success",
            last_error=None,
            next_run_at="2026-04-10T12:05:00+00:00",
        )
        manager = _mock_manager([status])
        result = await list_scheduled_tasks(_=AUTH_TOKEN, manager=manager)
        assert result.count == 1
        assert result.tasks[0].name == "osv-poll"
        assert result.tasks[0].run_count == 7
        assert result.tasks[0].error_count == 1
        assert result.tasks[0].last_run_status == "success"
        assert result.tasks[0].next_run_at == "2026-04-10T12:05:00+00:00"

    @pytest.mark.asyncio
    async def test_surfaces_error_tasks(self):
        status = TaskStatus(
            name="flaky",
            interval_seconds=60.0,
            jitter_seconds=0.0,
            run_count=3,
            error_count=2,
            last_run_at="2026-04-10T12:00:00+00:00",
            last_run_status="error",
            last_error="RuntimeError: boom",
            next_run_at="2026-04-10T12:01:00+00:00",
        )
        manager = _mock_manager([status])
        result = await list_scheduled_tasks(_=AUTH_TOKEN, manager=manager)
        assert result.tasks[0].last_run_status == "error"
        assert result.tasks[0].last_error == "RuntimeError: boom"
