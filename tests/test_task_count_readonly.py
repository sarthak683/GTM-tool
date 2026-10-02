"""The globally polled task badge must not repair or write task assignments."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_task_count_only_reads_assigned_open_tasks():
    from app.api.v1.endpoints.tasks import get_task_count

    session = SimpleNamespace(
        execute=AsyncMock(return_value=Mock(scalar_one=Mock(return_value=7))),
        commit=AsyncMock(),
    )
    user = SimpleNamespace(id=uuid4())

    assert await get_task_count(session, user) == {"open": 7}
    session.execute.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduled_reconciliation_repairs_assignments_in_manual_mode(monkeypatch):
    from app.config import settings
    from app.services import tasks
    from app.tasks.health import _async_reconcile_recent_deal_tasks

    session = SimpleNamespace(commit=AsyncMock())

    class SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_args):
            return False

    repair = AsyncMock()
    monkeypatch.setattr(settings, "ENABLE_SYSTEM_TASKS", False)
    monkeypatch.setattr("app.database.task_session", lambda: SessionContext())
    monkeypatch.setattr(tasks, "backfill_open_task_assignments", repair)

    assert await _async_reconcile_recent_deal_tasks() == 0
    repair.assert_awaited_once_with(session)
    session.commit.assert_awaited_once()
