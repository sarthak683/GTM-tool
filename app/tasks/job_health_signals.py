"""Auto-record scheduled-job health via Celery signals.

A single ``task_postrun`` handler upserts each *beat-scheduled* task's last run,
status, error, and duration into the ``job_health`` table — no per-task code, so
new beat jobs are tracked automatically. This powers the admin "System Health"
panel, turning silent scheduler failures (a dead report, a stalled sync) into a
visible red badge.

Design notes:
- Only beat-scheduled task names are recorded (filtered against the live
  ``beat_schedule``), so the table stays one row per scheduled job rather than
  exploding with ad-hoc/user-triggered tasks.
- The DB write runs in its own fresh event loop (the task's own loop has already
  closed by post-run) and is fully guarded — a tracking failure must never break
  the task it is observing.
- A task that runs and returns ``{"status": "skipped"}`` still counts as a
  successful *run*; that's the point — it proves the scheduler is alive.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from celery.signals import task_postrun, task_prerun

logger = logging.getLogger(__name__)

# task_id -> perf-counter start; set in prerun, consumed in postrun (same worker
# process handles both for a given task, so an in-memory dict is sufficient).
_STARTS: dict[str, float] = {}


def _scheduled_task_names(app) -> set[str]:
    try:
        schedule = app.conf.beat_schedule or {}
        return {entry.get("task") for entry in schedule.values() if entry.get("task")}
    except Exception:
        return set()


def _run(coro) -> None:
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(coro)
    finally:
        asyncio.set_event_loop(None)
        loop.close()


async def _record(task_name: str, status: str, error: str | None, duration_ms: int | None) -> None:
    from sqlalchemy import select

    from app.database import task_session
    from app.models.job_health import JobHealth

    now = datetime.utcnow()
    async with task_session() as session:
        row = (
            await session.execute(select(JobHealth).where(JobHealth.task_name == task_name))
        ).scalar_one_or_none()
        if row is None:
            row = JobHealth(task_name=task_name, runs_total=0, failures_total=0)
        row.last_run_at = now
        row.last_status = status
        row.last_duration_ms = duration_ms
        row.runs_total = (row.runs_total or 0) + 1
        if status == "success":
            row.last_success_at = now
            row.last_error = None
        else:
            row.failures_total = (row.failures_total or 0) + 1
            row.last_error = (error or "")[:1000]
        row.updated_at = now
        session.add(row)
        await session.commit()


@task_prerun.connect
def _on_prerun(task_id=None, task=None, **_kwargs) -> None:
    try:
        if task is not None and task.name in _scheduled_task_names(task.app):
            _STARTS[task_id] = time.perf_counter()
    except Exception:  # pragma: no cover - never let tracking break the task
        pass


@task_postrun.connect
def _on_postrun(task_id=None, task=None, retval=None, state=None, **_kwargs) -> None:
    try:
        if task is None or task.name not in _scheduled_task_names(task.app):
            return
        start = _STARTS.pop(task_id, None)
        duration_ms = int((time.perf_counter() - start) * 1000) if start is not None else None
        if state == "SUCCESS":
            status, error = "success", None
        else:
            status, error = "failure", str(retval)
        _run(_record(task.name, status, error, duration_ms))
    except Exception:  # pragma: no cover - never let tracking break the task
        logger.warning("job_health: failed to record %s", getattr(task, "name", "?"), exc_info=True)
